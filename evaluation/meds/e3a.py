"""E3a: the same models on the source MEDS and on the CDM in MEDS, with the same labels.

For one side (a MEDS dataset) and one task (labels from labels.py), with the same settings on
both sides (docs/evaluation.md, E3a):
1. MEDS-Tab: describe the codes, tabularize at the labels' prediction times (TABULARIZATION),
   and fit XGBoost with an Optuna search of N_TRIALS trials, scored on the tuning split;
2. an L1 logistic regression with Cyclops on the same feature matrices (MEDS-Tab's own loader,
   so the same codes, windows and aggregations), its prior variance chosen by Cyclops'
   cross-validation on the training split: `cyclops_lr` as PLP does it (columns scaled by their
   maximum), and `cyclops_lr_quintiles` with each value column also as quintile indicators;
3. predictions on the held-out split in <work>/<task>/{xgboost,cyclops_lr,cyclops_lr_quintiles}
   .parquet.

Usage: uv run python e3a.py <MEDS dataset dir> <task labels dir> <work dir> [--steps ...]
"""

import argparse
import os
import subprocess
import time
from pathlib import Path

import threads

# this process (MEDS-Tab's loader, Cyclops) stays within the budget too; set before Polars loads
os.environ.update(threads.env(1))

import numpy as np  # noqa: E402
import polars as pl  # noqa: E402

TABULARIZATION = [
    "tabularization.aggs=[code/count,value/sum,value/min,value/max]",
    "tabularization.window_sizes=[1d,7d,30d,full]",
    "tabularization.min_code_inclusion_count=10",
]
N_TRIALS = 20
WORKERS = threads.workers(16)  # MEDS-Tab tabularization workers


def run(cmd: list[str], workers: int = 1) -> None:
    print(" ".join(cmd), flush=True)
    subprocess.run(cmd, check=True, env=threads.env(workers))


def tabularize(dataset: Path, labels: Path, work: Path) -> None:
    common = [f"input_dir={dataset}/data", f"output_dir={work}", "do_overwrite=False"]
    run(["meds-tab-describe", *common])
    run(["meds-tab-tabularize-static", *common, f"input_label_dir={labels}", *TABULARIZATION])
    run(
        [
            "meds-tab-tabularize-time-series",
            "--multirun",
            f"worker=range(0,{WORKERS})",
            "hydra/launcher=joblib",
            f"hydra.launcher.n_jobs={WORKERS}",
            *common,
            f"input_label_dir={labels}",
            *TABULARIZATION,
        ],
        WORKERS,
    )
    # MEDS-Tab 0.2.0 wrote empty value matrices (issue #168); a dataset with numeric values must
    # have some
    values = (work / "tabularize").glob("train/*/*/value/*.npz")
    stored = sum(np.load(f)["array"].shape[1] for f in values)
    if stored == 0:
        raise ValueError(f"no stored values in {work}/tabularize: value aggregations are empty")
    print(f"value matrices: {stored} stored entries", flush=True)


def model_overrides(dataset: Path, labels: Path, work: Path) -> list[str]:
    return [
        f"input_dir={dataset}/data",
        f"output_dir={work}",
        "task_name=null",
        "do_overwrite=False",
        f"input_tabularized_cache_dir={work}/tabularize",
        f"input_label_cache_dir={labels}",
        *TABULARIZATION,
    ]


def xgboost(dataset: Path, labels: Path, work: Path) -> None:
    run(
        [
            "meds-tab-xgboost",
            "--multirun",
            *model_overrides(dataset, labels, work),
            f"output_model_dir={work}/xgboost",
            f"hydra.sweeper.n_trials={N_TRIALS}",
            "hydra.sweeper.n_jobs=1",
            # MEDS-Tab's model config sets nthread: 1, which overrides the thread environment
            f"model_launcher.model.nthread={threads.CPUS}",
        ]
    )
    best = sorted((work / "xgboost").glob("*/best_trial/held_out_predictions.parquet"))[-1]
    pl.read_parquet(best).write_parquet(work / "xgboost.parquet")


def _dataset(dataset: Path, labels: Path, work: Path, split: str):
    """MEDS-Tab's TabularDataset for a split, configured as the XGBoost run is."""
    from hydra import compose, initialize_config_module
    from MEDS_tabular_automl.tabular_dataset import TabularDataset

    with initialize_config_module("MEDS_tabular_automl.configs", version_base=None):
        cfg = compose(
            "launch_model",
            overrides=[
                *model_overrides(dataset, labels, work),
                f"output_model_dir={work}/cyclops_lr",
            ],
        )
    return TabularDataset(cfg, split=split)


def _value_columns(ds) -> np.ndarray:
    """Which of the loader's columns are value aggregations: per window and aggregation file, its
    codes in the inclusion set, in the order the loader stacks them."""
    from MEDS_tabular_automl.describe_codes import get_feature_columns
    from MEDS_tabular_automl.file_name import get_model_files
    from MEDS_tabular_automl.utils import get_feature_indices

    features = get_feature_columns(ds.cfg.tabularization.filtered_code_metadata_fp)
    mask = []
    for f in get_model_files(ds.cfg, ds.split, ds._data_shards[0]):
        static = f.stem in ("first", "present")
        agg = f"static/{f.stem}" if static else f"{f.parent.stem}/{f.stem}"
        n = sum(i in ds.codes_set for i in get_feature_indices(agg, features))
        mask += [agg.startswith("value/")] * n
    return np.asarray(mask)


def cyclops_lr(dataset: Path, labels: Path, work: Path, binned: bool = False) -> None:
    import scipy.sparse as sp
    from cyclops import Control, CyclopsData, CyclopsModel, Prior

    train = _dataset(dataset, labels, work, "train")
    X, y = train.get_data()
    held = _dataset(dataset, labels, work, "held_out")
    Xh, yh = held.get_data()
    if binned:
        values = _value_columns(train)
        if len(values) != X.shape[1]:
            raise ValueError(f"{len(values)} column names for {X.shape[1]} columns")
        # scaled raw values alone can be shrunk out by the L1 prior (hyponatremia, source: 0.717
        # -> 0.788 held-out AUROC; docs/evaluation-results.md, E3a)
        from models import quintiles

        X, Xh = quintiles(X, Xh, values)
    # scale each column by its maximum absolute value on the training split, as PLP normalises
    scale = np.asarray(abs(X).max(axis=0).todense()).ravel()
    scale[scale == 0] = 1
    D = sp.diags(1 / scale)
    X, Xh = sp.csc_matrix(X @ D), sp.csc_matrix(Xh @ D)
    start = time.time()
    data = CyclopsData.from_arrays(X, np.asarray(y, dtype=float), "lr", add_intercept=True)
    fit = CyclopsModel(
        data, prior=Prior("laplace", variance="cv"), control=Control(threads=threads.CPUS)
    ).fit()
    coef = np.asarray(fit.coefficients)
    risk = 1 / (1 + np.exp(-(coef[0] + Xh @ coef[1:])))
    print(
        f"cyclops: {time.time() - start:.0f} s,",
        f"{np.count_nonzero(coef[1:])} of {X.shape[1]} features",
    )
    # rows follow the label files in the loader's shard order
    rows = pl.concat(
        pl.read_parquet(labels / "held_out" / f"{shard}.parquet").select(
            "subject_id", "prediction_time", "boolean_value"
        )
        for shard in held._data_shards
    )
    if (
        rows.height != len(risk)
        or not (rows["boolean_value"].to_numpy() == np.asarray(yh, dtype=bool)).all()
    ):
        raise ValueError("held-out rows do not line up with the label files")
    out = work / ("cyclops_lr_quintiles.parquet" if binned else "cyclops_lr.parquet")
    rows.with_columns(predicted_boolean_probability=risk).write_parquet(out)


if __name__ == "__main__":
    p = argparse.ArgumentParser()
    p.add_argument("dataset", type=Path)
    p.add_argument("labels", type=Path)
    p.add_argument("work", type=Path)
    steps = {
        "tabularize": tabularize,
        "xgboost": xgboost,
        "cyclops_lr": cyclops_lr,
        "cyclops_lr_quintiles": lambda *a: cyclops_lr(*a, binned=True),
    }
    p.add_argument("--steps", nargs="+", choices=steps, default=list(steps))
    a = p.parse_args()
    a.work.mkdir(parents=True, exist_ok=True)
    for step in a.steps:
        t0 = time.time()
        steps[step](a.dataset, a.labels, a.work)
        print(f"{step}: {time.time() - t0:.0f} s", flush=True)
