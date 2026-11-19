"""E3: features, models and the source-vs-CDM comparison, each cached by what it depends on.

  uv run python e3.py tune <source MEDS>                 # once: LightGBM hyperparameters per task
  uv run python e3.py all --source <MEDS> --cdm <MEDS>   # both sides as needed, then compare
  uv run python e3.py run <side> <MEDS> [--final]        # one side
  uv run python e3.py compare <source MEDS> <cdm MEDS>
  uv run python e3.py seed-lr <source MEDS>              # freeze the LRs' prior variances
  uv run python e3.py run source <MEDS> --ladder         # FeatureExtraction-like steps, then:
  uv run python e3.py ladder-report source <MEDS>

Layout under <root>:
- <side>/<data>/features-<f>/: featurize.py's output, where <data> hashes the MEDS dataset's file
  names, sizes and times, and <f> hashes featurize.py;
- <side>/<data>/predictions-<f>/<task>/<model>-<key>.parquet (subject_id, prediction_time,
  boolean_value, predicted_boolean_probability), with .txt (fit summary) and .log, where <key>
  hashes the model's code, constants and frozen hyperparameters;
- params/lr-<f>/<task>.json: each LR's frozen prior variance (`seed-lr`: those the source side's
  latest fits chose); routine runs fit once at it, final runs choose it by cross-validation. The
  LRs' <key> includes the variance and the Cyclops build (cyclops_build);
- params/<f>-<search>/<task>.json: the LightGBM hyperparameters found by `tune` on the source side,
  where <search> hashes the search's code and inputs. When those change, routine runs keep the
  latest earlier hyperparameters for the same features (frozen_params); `all --final` re-tunes
  first, and `run --final` needs `tune`;
- compare/<source data>-<cdm data>-<f>/: compare.py per task and model, and summary.csv.

A rebuilt CDM in MEDS re-runs only the CDM side; a change to featurize.py re-runs everything; a
change to one model re-runs that model. Models: `lightgbm` (frozen hyperparameters), the two
Cyclops LRs, with --final `xgboost` (a full search per task, hours on the largest tasks), and with
--decompose the steps between the LR and the trees, and with --ladder the steps from inputs
shaped like FeatureExtraction's (binary presence in two windows, plus age, which featurize.py does
not make; not FeatureExtraction's default set) to the full features (models.ladder_inputs;
ladder/<side>-<data>-<f>.csv from `ladder-report`).

Each model fit is a subprocess with THREADS threads under a memory limit of MEMORY (systemd-run
--user, no swap), SLOTS at a time, so a fit that needs more is stopped alone instead of starving
the server.
"""

import argparse
import hashlib
import inspect
import json
import os
import re
import shlex
import shutil
import subprocess
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import threads

HERE = Path(__file__).parent
REPO = HERE.parents[1]
ROOT = REPO / "data/eval/mimic-iv-3.1/e3"
LABELS = REPO / "data/meds/labels"
MODELS = ("lightgbm", "cyclops_lr", "cyclops_lr_quintiles")
FINAL = ("xgboost",)
DECOMPOSITION = ("cyclops_lr_binary", "cyclops_lr_counts", "lightgbm_stumps")
VALUE_AGGS = ("sum", "min", "max", "first")
SLOTS, THREADS, MEMORY = 3, 32, "60G"
SELECTED: list[str] | None = None  # --tasks


def _hash(*parts) -> str:
    h = hashlib.sha256()
    for p in parts:
        h.update(p.encode() if isinstance(p, str) else p)
    return h.hexdigest()[:10]


def features_hash() -> str:
    return _hash((HERE / "featurize.py").read_bytes())


def data_hash(dataset: Path) -> str:
    files = sorted((dataset / "data").glob("*/*.parquet"))
    stamps = (f"{f.relative_to(dataset)}:{f.stat().st_size}:{f.stat().st_mtime_ns}" for f in files)
    return _hash(*stamps)


def _code(*functions) -> str:
    import models

    names = ("N_TRIALS", "SEED", "MIN_NONZERO", "MAX_ROUNDS", "PATIENCE", "LR_FOLDS", "LR_START")
    constants = {k: getattr(models, k) for k in names}
    # every model's inputs go through redundant_columns (job) and the tree models' through
    # nonzero_counts (kept_columns)
    shared = (models.redundant_columns, models.nonzero_counts)
    return json.dumps(constants) + "".join(inspect.getsource(f) for f in (*shared, *functions))


def search_hash() -> str:
    import models

    return _hash(_code(models.lightgbm_search, models._lightgbm_train, models.kept_columns))


def cyclops_build() -> str:
    """The installed Cyclops: its version and a hash of its compiled extension, so that predictions
    of the LRs rerun when Cyclops is rebuilt from another commit."""
    import importlib.metadata

    from cyclops import _cyclops

    digest = hashlib.sha256(Path(_cyclops.__file__).read_bytes()).hexdigest()[:10]
    return f"{importlib.metadata.version('ohdsi-cyclops')}-{digest}"


LR_MODELS = ("cyclops_lr", "cyclops_lr_quintiles", "cyclops_lr_binary", "cyclops_lr_counts")
# --ladder: LightGBM and the LR on each step of models.ladder_inputs, the LRs at the frozen
# variance of the existing LR with the nearest inputs
LADDER_STEPS = ("fe0", "fe", "fe_short", "fe_values", "full_age")
LADDER_MODELS = {
    f"{b}_ladder_{s}": (b, s) for b in ("lightgbm", "cyclops_lr") for s in LADDER_STEPS
}
LADDER_VARIANCE = {"fe0": "cyclops_lr_binary", "fe": "cyclops_lr_binary",
                   "fe_short": "cyclops_lr_binary", "fe_values": "cyclops_lr_quintiles",
                   "full_age": "cyclops_lr"}  # fmt: skip
MODEL_FILTER: list[str] | None = None  # --models


def is_lr(model: str) -> bool:
    return model in LR_MODELS or LADDER_MODELS.get(model, ("",))[0] == "cyclops_lr"


def is_lightgbm(model: str) -> bool:
    base = LADDER_MODELS.get(model, ("",))[0]
    return model in ("lightgbm", "lightgbm_stumps") or base == "lightgbm"


def birth_times(features: Path):
    """subject_id -> birth time (the MEDS_BIRTH event) in the dataset the features were made
    from (the side's manifest), cached beside the features."""
    import polars as pl

    out = features / "birth.parquet"
    if not out.exists():
        dataset = Path(json.loads((features.parent / "manifest.json").read_text())["dataset"])
        births = (
            pl.scan_parquet(str(dataset / "data/*/*.parquet"))
            .filter(pl.col("code").str.starts_with("MEDS_BIRTH"))
            .group_by("subject_id")
            .agg(birth=pl.col("time").min().cast(pl.Datetime("us")))
            .collect()
        )
        tmp = out.with_suffix(f".{os.getpid()}.tmp")
        births.write_parquet(tmp)
        tmp.rename(out)
    return pl.read_parquet(out)


def label_ages(features: Path, labels: list) -> list:
    """Each label's age in years at its prediction time, in the labels' order (NaN without a
    birth event)."""
    import polars as pl

    births = birth_times(features)
    out = []
    for lab in labels:
        t = lab.select("subject_id", t=pl.col("prediction_time").cast(pl.Datetime("us")))
        a = (
            t.with_row_index("_i")
            .join(births, on="subject_id", how="left")
            .sort("_i")
            .select(age=(pl.col("t") - pl.col("birth")).dt.total_seconds() / (365.25 * 86400))
        )
        out.append(a["age"].cast(pl.Float64).fill_null(float("nan")).to_numpy())
    return out


def lr_params_dir(root: Path) -> Path:
    return root / "params" / f"lr-{features_hash()}"


def frozen_lr_variance(root: Path, task: str, model: str, final: bool) -> float | None:
    """The LR's frozen prior variance for a task (lr_params_dir, written by `seed-lr`), or None:
    final runs choose it by cross-validation, as does a routine run without one."""
    p = lr_params_dir(root) / f"{task}.json"
    if final or not p.exists():
        return None
    if model in LADDER_MODELS:
        model = LADDER_VARIANCE[LADDER_MODELS[model][1]]
    return json.loads(p.read_text()).get(model)


def seed_lr(root: Path, dataset: Path) -> None:
    """Freeze each task's LR prior variances at those the source side's latest LR fits chose (read
    from their fit summaries, `variance [v]`), one JSON per task in lr_params_dir."""
    preds = side_dir(root, "source", dataset) / f"predictions-{features_hash()}"
    out = lr_params_dir(root)
    out.mkdir(parents=True, exist_ok=True)
    for task in tasks():
        chosen = {}
        for model in LR_MODELS:
            fits = sorted((preds / task).glob(f"{model}-*.txt"), key=lambda f: f.stat().st_mtime)
            found = re.findall(r"variance \[([0-9.eE+-]+)\]", fits[-1].read_text()) if fits else []
            if found:
                chosen[model] = float(found[0])
                chosen[f"{model}_from"] = fits[-1].name
        (out / f"{task}.json").write_text(json.dumps(chosen, indent=1) + "\n")
        print(task, {k: v for k, v in chosen.items() if not k.endswith("_from")}, flush=True)


def model_key(model: str, params: str = "") -> str:
    import models

    code = {
        "lightgbm": (models.lightgbm_fit, models._lightgbm_train, models.kept_columns),
        "cyclops_lr": (models.cyclops_lr, models.quintiles),
        "cyclops_lr_quintiles": (models.cyclops_lr, models.quintiles),
        "xgboost": (models.xgboost_search, models.kept_columns),
        "cyclops_lr_binary": (models.cyclops_lr, models.lr_inputs),
        "cyclops_lr_counts": (models.cyclops_lr, models.lr_inputs),
        "lightgbm_stumps": (models.lightgbm_stumps, models.kept_columns),
    }
    ladder = (models.ladder_inputs, models.age_groups, models.quintiles, label_ages, birth_times)
    for name, (base, _) in LADDER_MODELS.items():
        code[name] = code[base] + ladder
    code = code[model]
    if model == "lightgbm_stumps":
        code += (f"{models.STUMP_RATE} {models.STUMP_ROUNDS}",)
    return _hash(
        model,
        _code(*(c for c in code if callable(c))),
        *(c for c in code if isinstance(c, str)),
        params,
    )


def tasks() -> dict[str, Path]:
    import labels

    names = {t.replace("/", "_"): LABELS / t for t in labels.DEFAULT}
    return {k: v for k, v in names.items() if SELECTED is None or k in SELECTED}


def side_dir(root: Path, side: str, dataset: Path) -> Path:
    return root / side / data_hash(dataset)


def params_dir(root: Path) -> Path:
    return root / "params" / f"{features_hash()}-{search_hash()}"


def frozen_params(root: Path, task: str, final: bool) -> Path:
    """The task's frozen LightGBM hyperparameters: from the current search (params_dir), or, outside
    a final run, from the latest earlier search on the same features. Routine runs so keep them
    across changes to the search's inputs (models.kept_columns, redundant_columns); a final run
    re-tunes."""
    current = params_dir(root) / f"{task}.json"
    if current.exists() or final:
        return current
    # only searches: <features>-<search hash>, never another kind of parameter (lr-<features>)
    earlier = (
        p
        for p in root.glob(f"params/{features_hash()}-*/{task}.json")
        if re.fullmatch(r"[0-9a-f]{10}", p.parent.name.split("-", 1)[1])
    )
    return max(earlier, key=lambda p: p.stat().st_mtime, default=current)


def features(root: Path, side: str, dataset: Path) -> Path:
    import featurize

    out = side_dir(root, side, dataset) / f"features-{features_hash()}"
    if not (out / "done.json").exists():
        if out.exists():
            shutil.rmtree(out)
        featurize.featurize(dataset, tasks(), out, workers=64)
    (out.parent / "manifest.json").write_text(json.dumps({"side": side, "dataset": str(dataset)}))
    return out


# --- one model fit, in its own process ---


def load(features: Path, task: str):
    """(X, y, labels) for train, tuning and held-out, and the feature table (featurize.py)."""
    import numpy as np
    import polars as pl
    import scipy.sparse as sp

    out = {}
    for split in ("train", "tuning", "held_out"):
        X = sp.load_npz(features / task / f"{split}.npz").tocsr()
        labels = pl.read_parquet(features / task / f"{split}_labels.parquet")
        out[split] = (X, labels["boolean_value"].to_numpy().astype(np.float32), labels)
    return out, pl.read_parquet(features / "features.parquet")


def job(
    features: Path,
    task: str,
    model: str,
    out: Path,
    params: Path | None,
    threads: int,
    variance: float | None = None,
):
    import models
    import numpy as np
    import polars as pl

    data, table = load(features, task)
    (X, y, _), (Xt, yt, _), (Xh, _, lh) = data["train"], data["tuning"], data["held_out"]
    agg = table["agg"].to_numpy()
    codes, windows = table["code"].to_numpy(), table["window"].to_numpy()
    redundant = models.redundant_columns(X, codes, agg, windows)
    removed = table.filter(pl.Series(redundant))["name"].to_list()
    print(f"redundant columns removed: {removed}", flush=True)
    X, Xt, Xh, agg = X[:, ~redundant], Xt[:, ~redundant], Xh[:, ~redundant], agg[~redundant]
    windows = windows[~redundant]
    values = np.isin(agg, VALUE_AGGS)
    if model == "lightgbm_search":
        best, info = models.lightgbm_search(X, y, Xt, yt, threads=threads)
        out.write_text(json.dumps(best, indent=1) + "\n")
        print(info, flush=True)
        return
    if model in LADDER_MODELS:
        base, step = LADDER_MODELS[model]
        ages = label_ages(features, [data[s][2] for s in ("train", "tuning", "held_out")])
        print(f"ages: {np.isnan(ages[0]).sum()} training labels without one", flush=True)
        X, Xt, Xh = models.ladder_inputs([X, Xt, Xh], agg, windows, step, ages)
        if base == "lightgbm":
            frozen = json.loads(params.read_text())
            risk, info = models.lightgbm_fit(X, y, Xt, yt, Xh, frozen, threads)
        else:
            risk, info = models.cyclops_lr(X, y, Xh, threads=threads, variance=variance)
        info = f"{step}, {X.shape[1]} columns: {info}"
    elif model == "lightgbm":
        risk, info = models.lightgbm_fit(X, y, Xt, yt, Xh, json.loads(params.read_text()), threads)
    elif model == "lightgbm_stumps":
        frozen = json.loads(params.read_text())
        risk, info = models.lightgbm_stumps(X, y, Xt, yt, Xh, frozen, threads)
    elif model in ("cyclops_lr_binary", "cyclops_lr_counts"):
        A, Ah = models.lr_inputs(X, Xh, agg, model.rsplit("_", 1)[1])
        risk, info = models.cyclops_lr(A, y, Ah, threads=threads, variance=variance)
    elif model == "xgboost":
        risk, info = models.xgboost_search(X, y, Xt, yt, Xh, threads=threads)
    else:
        binned = values if model == "cyclops_lr_quintiles" else None
        risk, info = models.cyclops_lr(X, y, Xh, threads=threads, values=binned, variance=variance)
    tmp = out.with_suffix(".tmp")
    lh.with_columns(predicted_boolean_probability=risk).write_parquet(tmp)
    tmp.rename(out)
    out.with_suffix(".txt").write_text(info + "\n")
    print(info, flush=True)


# --- scheduling ---


def _launch(name: str, args: list[str], log: Path) -> bool:
    env_threads = {v: str(THREADS) for v in threads.VARIABLES}
    cmd = [sys.executable, str(HERE / "e3.py"), "job", *args, "--threads", str(THREADS)]
    if shutil.which("systemd-run"):
        cmd = ["systemd-run", "--user", "--scope", "--quiet", "-p", f"MemoryMax={MEMORY}",
               "-p", "MemorySwapMax=0", *[f"--setenv={k}={v}" for k, v in env_threads.items()],
               *cmd]  # fmt: skip
    # E3_LAUNCH_PREFIX: a command each fit runs under, e.g. a shared server's booking script
    # ("~/bench/booked.sh bulk"), so that other users' timed runs can go between fits
    cmd = [os.path.expanduser(c) for c in shlex.split(os.environ.get("E3_LAUNCH_PREFIX", ""))] + cmd
    start = time.time()
    with log.open("w") as f:
        env = os.environ | env_threads
        code = subprocess.run(cmd, stdout=f, stderr=subprocess.STDOUT, env=env).returncode
    last = log.read_text().strip().splitlines()[-1:] or [""]
    status = "ok" if code == 0 else f"FAILED (exit {code})"
    print(f"{name}: {status}, {time.time() - start:.0f} s: {last[0][:160]}", flush=True)
    return code == 0


def _run_jobs(jobs: list[tuple[str, list[str], Path]]) -> bool:
    with ThreadPoolExecutor(SLOTS) as pool:
        return all(pool.map(lambda j: _launch(*j), jobs))


def _size(features: Path, task: str) -> int:
    return (features / task / "train.npz").stat().st_size


def tune(root: Path, dataset: Path) -> None:
    feats = features(root, "source", dataset)
    out = params_dir(root)
    out.mkdir(parents=True, exist_ok=True)
    todo = sorted((t for t in tasks() if not (out / f"{t}.json").exists()),
                  key=lambda t: -_size(feats, t))  # fmt: skip
    jobs = [(f"{t} search", ["--features", str(feats), "--task", t, "--model", "lightgbm_search",
              "--out", str(out / f"{t}.json")], out / f"{t}.log") for t in todo]  # fmt: skip
    if not _run_jobs(jobs):
        raise SystemExit("a search failed; see its log")


def planned_fits(root: Path, final: bool, decompose: bool, ladder: bool = False):
    """(task, model, file name, extra job arguments) of every fit a run makes (only --models, if
    given). The names do not depend on the side: a comparison pairs the fits of one run, never
    those an earlier one left."""
    build = cyclops_build()
    chosen = MODELS + (FINAL if final else ()) + (DECOMPOSITION if decompose else ())
    chosen += tuple(LADDER_MODELS) if ladder else ()
    for task in tasks():
        for model in chosen:
            if MODEL_FILTER is not None and model not in MODEL_FILTER:
                continue
            extra, frozen = [], ""
            if is_lightgbm(model):
                p = frozen_params(root, task, final)
                if not p.exists():
                    raise SystemExit(f"no hyperparameters for {task}: run `e3.py tune` first")
                extra, frozen = ["--params", str(p)], p.read_text()
            if is_lr(model):
                v = frozen_lr_variance(root, task, model, final)
                extra = [] if v is None else ["--variance", repr(v)]
                frozen = json.dumps({"variance": v, "cyclops": build})
            yield task, model, f"{model}-{model_key(model, frozen)}.parquet", extra


def run_side(
    root: Path, side: str, dataset: Path, final: bool, decompose: bool = False, ladder: bool = False
) -> Path:
    t0 = time.time()
    feats = features(root, side, dataset)
    preds = side_dir(root, side, dataset) / f"predictions-{features_hash()}"
    for task in tasks():
        p = frozen_params(root, task, final)
        if p.exists() and p.parent != params_dir(root):
            print(f"{task}: LightGBM hyperparameters of an earlier search, {p.parent.name}"
                  " (its inputs changed since; a final run re-tunes)", flush=True)  # fmt: skip
    jobs = []
    fits = planned_fits(root, final, decompose, ladder)
    planned = sorted(fits, key=lambda f: -_size(feats, f[0]))
    for task, model, name, extra in planned:
        out = preds / task / name
        if out.exists():
            continue
        out.parent.mkdir(parents=True, exist_ok=True)
        args = ["--features", str(feats), "--task", task, "--model", model,
                "--out", str(out), *extra]  # fmt: skip
        jobs.append((f"{side} {task} {model}", args, out.with_suffix(".log")))
    ok = _run_jobs(jobs)
    print(f"{side}: {len(jobs)} fits, {time.time() - t0:.0f} s, {preds}", flush=True)
    if not ok:
        raise SystemExit("a fit failed; see its log")
    return preds


def _compare_one(args):
    import compare
    import polars as pl

    task, model, s, c, out = args
    r = compare.compare(pl.read_parquet(s), pl.read_parquet(c))
    r = r.with_columns(task=pl.lit(task), model=pl.lit(model))
    r.write_csv(out / f"{task}_{model}.csv")
    return r


def compare_sides(root: Path, source: Path, cdm: Path, final: bool, decompose: bool = False):
    import multiprocessing
    from concurrent.futures import ProcessPoolExecutor

    import polars as pl

    f = features_hash()
    s_dir = side_dir(root, "source", source) / f"predictions-{f}"
    c_dir = side_dir(root, "cdm", cdm) / f"predictions-{f}"
    out = root / "compare" / f"{data_hash(source)}-{data_hash(cdm)}-{f}"
    out.mkdir(parents=True, exist_ok=True)
    jobs, missing = [], []
    for task, model, name, _ in planned_fits(root, final, decompose):
        s, c = s_dir / task / name, c_dir / task / name
        if s.exists() and c.exists():
            jobs.append((task, model, s, c, out))
        else:
            missing.append(f"{task} {model}")
    if missing:
        print(f"not on both sides, so not compared: {', '.join(missing)}", flush=True)
    spawn = multiprocessing.get_context("spawn")
    with ProcessPoolExecutor(min(32, len(jobs)), mp_context=spawn) as pool:
        results = list(pool.map(_compare_one, jobs))
    summary = pl.concat(results)
    summary.write_csv(out / "summary.csv")
    with pl.Config(tbl_rows=-1, tbl_cols=-1, tbl_width_chars=200, float_precision=3):
        print(summary.filter(pl.col("metric") == "auroc").select(
            "task", "model", "source", "cdm", "difference", "ci90_low", "ci90_high", "equivalent"
        ))  # fmt: skip
    print(f"comparison: {out}", flush=True)


LADDER_PAIRS = (  # (from, to, what the step adds), for each base model
    ("fe0", "fe", "age groups"),
    ("fe", "fe_short", "1-day and 7-day windows"),
    ("fe_short", "fe_values", "values as quintile indicators"),
    ("fe_values", "full_age", "counts and raw values"),
    ("fe", "full_age", "all of the extra featurization"),
    ("full", "full_age", "age groups, on the full features"),
)


def ladder_report(root: Path, side: str, dataset: Path) -> None:
    """Held-out AUROC of each ladder step and the paired difference each step makes (compare.py's
    bootstrap over subjects), per task and base model, into ladder/<side>-<data>-<f>.csv."""
    import compare
    import polars as pl

    preds = side_dir(root, side, dataset) / f"predictions-{features_hash()}"
    names = {(t, m): n for t, m, n, _ in planned_fits(root, False, False, ladder=True)}
    rows = []
    for task in tasks():
        for base in ("lightgbm", "cyclops_lr"):

            def fit(step, task=task, base=base):
                model = base if step == "full" else f"{base}_ladder_{step}"
                path = preds / task / names.get((task, model), "-")
                return pl.read_parquet(path) if path.exists() else None

            for a, b, what in LADDER_PAIRS:
                fa, fb = fit(a), fit(b)
                if fa is None or fb is None:
                    continue
                r = compare.compare(fa, fb).filter(pl.col("metric") == "auroc").row(0, named=True)
                rows.append({"task": task, "model": base, "from": a, "to": b, "adds": what,
                             "auroc_from": r["source"], "auroc_to": r["cdm"],
                             "difference": r["difference"], "ci95_low": r["ci95_low"],
                             "ci95_high": r["ci95_high"]})  # fmt: skip
    if not rows:
        raise SystemExit("no ladder fits on this side yet: run `e3.py run <side> <MEDS> --ladder`")
    out = root / "ladder" / f"{side}-{data_hash(dataset)}-{features_hash()}.csv"
    out.parent.mkdir(parents=True, exist_ok=True)
    table = pl.DataFrame(rows)
    table.write_csv(out)
    with pl.Config(tbl_rows=-1, tbl_cols=-1, tbl_width_chars=220, float_precision=4):
        print(table.drop("from", "to"))
    print(f"ladder: {out}", flush=True)


if __name__ == "__main__":
    p = argparse.ArgumentParser()
    p.add_argument("--root", type=Path, default=ROOT)
    p.add_argument("--tasks", type=lambda v: v.split(","), help="comma-separated task names")
    p.add_argument("--slots", type=int, default=SLOTS)
    p.add_argument("--threads", type=int, default=THREADS)
    p.add_argument("--memory", default=MEMORY)
    p.add_argument("--models", type=lambda v: v.split(","), help="comma-separated model names")
    sub = p.add_subparsers(dest="command", required=True)
    t = sub.add_parser("tune")
    t.add_argument("source", type=Path)
    r = sub.add_parser("run")
    r.add_argument("side")
    r.add_argument("dataset", type=Path)
    r.add_argument("--final", action="store_true")
    r.add_argument("--decompose", action="store_true", help="also the LR-vs-trees decomposition")
    r.add_argument("--ladder", action="store_true", help="also FeatureExtraction-to-full steps")
    lr_ = sub.add_parser("ladder-report", help="what each --ladder step adds, on one side")
    lr_.add_argument("side")
    lr_.add_argument("dataset", type=Path)
    c = sub.add_parser("compare", help="the fits a `run` with the same options made")
    c.add_argument("source", type=Path)
    c.add_argument("cdm", type=Path)
    c.add_argument("--final", action="store_true")
    c.add_argument("--decompose", action="store_true")
    al = sub.add_parser("all")
    al.add_argument("--source", type=Path, required=True)
    al.add_argument("--cdm", type=Path, required=True)
    al.add_argument("--final", action="store_true")
    al.add_argument("--decompose", action="store_true", help="also the LR-vs-trees decomposition")
    j = sub.add_parser("job")
    j.add_argument("--features", type=Path, required=True)
    j.add_argument("--task", required=True)
    j.add_argument("--model", required=True)
    j.add_argument("--out", type=Path, required=True)
    j.add_argument("--params", type=Path)
    j.add_argument("--threads", type=int, default=THREADS)
    j.add_argument("--variance", type=float, help="a frozen LR prior variance")
    sl = sub.add_parser("seed-lr", help="freeze LR variances at the source side's latest choices")
    sl.add_argument("source", type=Path)
    a = p.parse_args()
    SELECTED, SLOTS, THREADS, MEMORY = a.tasks, a.slots, a.threads, a.memory
    MODEL_FILTER = a.models
    if a.command == "job":
        job(a.features, a.task, a.model, a.out, a.params, a.threads, a.variance)
    elif a.command == "seed-lr":
        seed_lr(a.root, a.source)
    elif a.command == "tune":
        tune(a.root, a.source)
    elif a.command == "run":
        run_side(a.root, a.side, a.dataset, a.final, a.decompose, a.ladder)
    elif a.command == "ladder-report":
        ladder_report(a.root, a.side, a.dataset)
    elif a.command == "compare":
        compare_sides(a.root, a.source, a.cdm, a.final, a.decompose)
    else:
        if a.final:  # final runs re-tune when the search's inputs have changed
            tune(a.root, a.source)
        run_side(a.root, "source", a.source, a.final, a.decompose)
        run_side(a.root, "cdm", a.cdm, a.final, a.decompose)
        compare_sides(a.root, a.source, a.cdm, a.final, a.decompose)
