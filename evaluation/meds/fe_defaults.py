"""E3 diagnostic: FeatureExtraction's default covariates against E3's features, on the CDM.

What does featurize.py add over what an OHDSI user gets from FeatureExtraction's defaults
(createDefaultCovariateSettings), with the same learners, labels and splits? FeatureExtraction
works in days, on the CDM's own dates, so the E3 labels of TASKS get three more inputs:

- fe_clean: the default covariates with the window ending the day before the prediction date for
  the 24-hour tasks (anything on that day may follow the prediction time), and on the prediction
  date for readmission (predicted at discharge); on a copy of the CDM in which billing diagnoses
  are dated at their visit's end, as omop_meds.py places them (the CDM dates them at the visit's
  start, which puts an admission's discharge diagnoses in its first day), with condition_era
  rebuilt from them;
- fe: fe_clean without DrugGroupEraOverlapping (LEAKY_ANALYSES): its drug eras end with the last
  exposure up to 30 days later, so on readmission an era overlaps the index date because the
  readmission repeats the drug; the analysis is computed at the index date whatever endDays is;
- fe_default: the defaults as they come (window ending on the prediction date, diagnoses at the
  visit's start), for the 24-hour tasks: what a default run gets, leaks included;
- day: E3's features from the CDM in MEDS with the prediction time moved to the end of fe_clean's
  last day. fe against day isolates the featurization; day against E3's CDM fits (at the
  prediction time) is what the day cutoff costs.

Readmission is scored without the positives readmitted on their discharge date (6% in MIMIC-IV
3.1), whose readmission a window ending on that date sees (same_day_readmissions).

Models, through `e3.py job`: LightGBM at the task's frozen hyperparameters, and the Cyclops LR at
the frozen variance of the binary LR (FeatureExtraction's covariates are nearly all indicators)
or, for `day`, of the main LR. FeatureExtraction's covariates are kept when present in at least
MIN_FRACTION of the training rows (PLP's default), and redundant ones are removed per analysis, as
in every E3 model.

  uv run python evaluation/meds/fe_defaults.py prepare <work>   # repo env: needs duckdb, mimic_omop
  Rscript evaluation/R/fe_covariates.R <work> fe_clean|fe_default
  <e3 venv python> fe_defaults.py matrices <work> fe_clean|fe_default
  <e3 venv python> fe_defaults.py matrices <work> fe   # from fe_clean's matrices
  <e3 venv python> fe_defaults.py day <work>
  <e3 venv python> fe_defaults.py fit <work>
  <e3 venv python> fe_defaults.py report <work>
"""

import argparse
import json
import os
import shutil
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import e3  # noqa: E402  (stdlib only at import)

REPO = HERE.parents[1]
CDM = REPO / "data/mimic-iv-3.1-cdm"
CDM_DB = REPO / "data/mimic-iv-3.1-cdm.duckdb"
CDM_MEDS = REPO / "data/meds/cdm-meds-v3"
SOURCE_MEDS = REPO / "data/meds/mimic-iv-3.1-meds-0.1.0"
# task: (label directory under e3.LABELS, cohort id, fe_clean's window end relative to the
# prediction date)
TASKS = {
    "mortality_in_hospital_first_24h": ("mortality/in_hospital/first_24h", 1, -1),
    "mortality_in_icu_first_24h": ("mortality/in_icu/first_24h", 2, -1),
    "readmission_general_hospital_30d": ("readmission/general_hospital/30d", 3, 0),
    "abnormal_lab_blood_chemistry_elevated_creatinine_first_24h": (
        "abnormal_lab/blood_chemistry/elevated_creatinine/first_24h", 4, -1),
    "abnormal_lab_cbc_thrombocytopenia_first_24h": (
        "abnormal_lab/cbc/thrombocytopenia/first_24h", 5, -1),
}  # fmt: skip
SPLITS = ("train", "tuning", "held_out")
MIN_FRACTION = 0.001
BILLING = 32821
DIAGNOSIS_VOCABULARIES = ("ICD9CM", "ICD10CM")
# CDM tables holding billing diagnoses, with their date and datetime columns
DATED = {
    "condition_occurrence": ("condition", "condition_start_date", "condition_start_datetime"),
    "observation": ("observation", "observation_date", "observation_datetime"),
    "measurement": ("measurement", "measurement_date", "measurement_datetime"),
    "procedure_occurrence": ("procedure", "procedure_date", "procedure_datetime"),
}
MODELS = ("lightgbm", "cyclops_lr")
LEAKY_ANALYSES = (413,)  # DrugGroupEraOverlapping
# (reference, alternative, what the difference measures)
PAIRS = (
    ("day", "fe", "FeatureExtraction's defaults instead of E3's features (same day cutoff)"),
    ("cdm", "day", "the day cutoff instead of the prediction time (E3's features)"),
    ("fe", "fe_clean", "DrugGroupEraOverlapping added (leaks readmission)"),
    ("fe_clean", "fe_default", "FeatureExtraction run as it comes (leaks) instead of clean"),
    ("cdm", "fe", "FeatureExtraction's defaults, clean, instead of E3's CDM fit"),
)


def e3_features() -> Path:
    return e3.side_dir(e3.ROOT, "cdm", CDM_MEDS) / f"features-{e3.features_hash()}"


def variants(task: str) -> tuple[str, ...]:
    # readmission's window ends on the prediction date either way, and its admission's diagnoses
    # are dated on or before that date either way: fe_default would repeat fe_clean
    if TASKS[task][2] == 0:
        return ("fe", "fe_clean", "day")
    return ("fe", "fe_clean", "fe_default", "day")


# --- prepare (repo env) ---


def _billed(table: str) -> tuple[str, str]:
    """SQL for a CDM table's billing diagnoses: the condition on x (the table), c (its source
    concept) and v (its visit), and the FROM clause that joins them."""
    prefix = DATED[table][0]
    vocabularies = ", ".join(f"'{v}'" for v in DIAGNOSIS_VOCABULARIES)
    billed = (
        f"x.{prefix}_type_concept_id = {BILLING} AND c.vocabulary_id IN ({vocabularies})"
        " AND v.visit_end_date IS NOT NULL"
    )
    joined = (
        f"cdm.{table} x LEFT JOIN cdm.concept c ON c.concept_id = x.{prefix}_source_concept_id"
        " LEFT JOIN cdm.visit_occurrence v ON v.visit_occurrence_id = x.visit_occurrence_id"
    )
    return billed, joined


def prepare(work: Path) -> None:
    import duckdb
    import polars as pl
    from mimic_omop.etl.eras import condition_era

    work.mkdir(parents=True, exist_ok=True)
    pl.DataFrame(
        {"name": list(TASKS), "cohort_id": [t[1] for t in TASKS.values()],
         "end_day": [t[2] for t in TASKS.values()]}
    ).write_csv(work / "tasks.csv")  # fmt: skip

    # one cohort row per E3 label, in the order of E3's feature matrices
    parts, offset = [], 0
    for task, (_, cohort, _) in TASKS.items():
        for split in SPLITS:
            lab = pl.read_parquet(e3_features() / task / f"{split}_labels.parquet")
            parts.append(
                lab.select("subject_id", pl.col("prediction_time").cast(pl.Datetime("us")))
                .with_row_index("position")
                .with_columns(
                    row_id=pl.int_range(pl.len(), dtype=pl.Int64) + offset,
                    task=pl.lit(task),
                    split=pl.lit(split),
                    cohort_definition_id=pl.lit(cohort),
                    cohort_start_date=pl.col("prediction_time").dt.date(),
                )  # fmt: skip
            )
            offset += lab.height
    rows = pl.concat(parts)
    rows.write_parquet(work / "rows.parquet")
    print(f"{rows.height} labels", flush=True)

    # billing diagnoses at their visit's end (omop_meds.py's rule), condition_era rebuilt
    clean = work / "clean"
    clean.mkdir(exist_ok=True)
    concept = pl.scan_parquet(CDM / "concept/*.parquet").select("concept_id", "vocabulary_id")
    visits = pl.scan_parquet(CDM / "visit_occurrence/*.parquet").select(
        "visit_occurrence_id", "visit_end_date", "visit_end_datetime"
    )
    co = pl.scan_parquet(CDM / "condition_occurrence/*.parquet")
    columns = co.collect_schema().names()
    billed = (
        (pl.col("condition_type_concept_id") == BILLING)
        & pl.col("vocabulary_id").is_in(DIAGNOSIS_VOCABULARIES)
        & pl.col("visit_end_date").is_not_null()
    )
    shifted = (
        co.join(concept, left_on="condition_source_concept_id", right_on="concept_id", how="left")
        .join(visits, on="visit_occurrence_id", how="left")
        .with_columns(
            condition_start_date=pl.when(billed).then("visit_end_date")
            .otherwise("condition_start_date"),
            condition_start_datetime=pl.when(billed)
            .then(pl.coalesce("visit_end_datetime", pl.col("visit_end_date").cast(pl.Datetime)))
            .otherwise("condition_start_datetime"),
            condition_end_date=pl.when(billed)
            .then(pl.max_horizontal("condition_end_date", "visit_end_date"))
            .otherwise("condition_end_date"),
        )
        .select(columns)
    )  # fmt: skip
    shifted.sink_parquet(clean / "condition_occurrence.parquet")
    condition_era(pl.scan_parquet(clean / "condition_occurrence.parquet")).sink_parquet(
        clean / "condition_era.parquet"
    )
    print("clean condition_occurrence and condition_era written", flush=True)

    # a copy of the CDM's DuckDB file: schema cdm as exported, schema clean with the billing
    # diagnoses moved, schema e3fe with the cohort table
    db = work / "cdm-fe.duckdb"
    if not db.exists():
        shutil.copyfile(CDM_DB, work / "cdm-fe.duckdb.tmp")
        (work / "cdm-fe.duckdb.tmp").rename(db)
    con = duckdb.connect(str(db))
    con.execute("SET memory_limit = '64GB'; SET threads = 32")
    con.execute(f"SET temp_directory = '{work / 'duckdb-tmp'}'")
    con.execute("CREATE SCHEMA IF NOT EXISTS e3fe")
    con.execute(
        "CREATE OR REPLACE TABLE e3fe.labels AS SELECT row_id, cohort_definition_id, subject_id,"
        " cohort_start_date, cohort_start_date AS cohort_end_date"
        f" FROM read_parquet('{work / 'rows.parquet'}')"
    )
    con.execute("DROP SCHEMA IF EXISTS clean CASCADE; CREATE SCHEMA clean")
    listed = "SELECT table_name FROM information_schema.tables WHERE table_schema = 'cdm'"
    tables = [r[0] for r in con.execute(listed).fetchall()]
    for t in tables:
        if t in ("condition_occurrence", "condition_era"):
            con.execute(
                f"CREATE TABLE clean.{t} AS SELECT * FROM read_parquet('{clean / t}.parquet')"
            )
        elif t in DATED:
            _, date, time = DATED[t]
            billed, joined = _billed(t)
            end_time = "coalesce(v.visit_end_datetime, CAST(v.visit_end_date AS TIMESTAMP))"
            con.execute(
                f"CREATE TABLE clean.{t} AS SELECT x.* REPLACE ("
                f" CASE WHEN {billed} THEN v.visit_end_date ELSE x.{date} END AS {date},"
                f" CASE WHEN {billed} THEN {end_time} ELSE x.{time} END AS {time}) FROM {joined}"
            )
        else:
            con.execute(f"CREATE VIEW clean.{t} AS SELECT * FROM cdm.{t}")
    for t, (_, date, _) in DATED.items():
        billed, joined = _billed(t)
        n, moved = con.execute(
            f"SELECT count(*) FILTER (WHERE {billed}),"
            f" count(*) FILTER (WHERE {billed} AND v.visit_end_date <> x.{date}) FROM {joined}"
        ).fetchone()
        print(f"clean.{t}: {n} billing diagnoses, {moved} to a later date", flush=True)
    con.close()

    # the day cutoff's label files (E3's layout), for `day`
    for path, _, end_day in TASKS.values():
        for f in sorted((e3.LABELS / path).glob("*/*.parquet")):
            lab = pl.read_parquet(f)
            dtype = lab.schema["prediction_time"]
            day = pl.col("prediction_time").cast(pl.Datetime("us")).dt.truncate("1d")
            cut = day + pl.duration(days=end_day + 1, microseconds=-1)
            dest = work / "labels-day" / path / f.relative_to(e3.LABELS / path)
            dest.parent.mkdir(parents=True, exist_ok=True)
            lab.with_columns(prediction_time=cut.cast(dtype)).write_parquet(dest)

    # readmission: positives whose next admission starts later on the prediction date, which a
    # window ending on that date sees
    pos = rows.filter(pl.col("task") == "readmission_general_hospital_30d").join(
        pl.concat([pl.read_parquet(e3_features() / "readmission_general_hospital_30d"
                                   / f"{s}_labels.parquet").with_row_index("position")
                   .with_columns(split=pl.lit(s)) for s in SPLITS]).select(
            "split", "position", "boolean_value"),
        on=["split", "position"],
    ).filter("boolean_value")  # fmt: skip
    starts = (
        pl.scan_parquet(CDM / "visit_occurrence/*.parquet")
        .filter(pl.col("visit_concept_id").is_in([9201, 262]))
        .select(
            subject_id="person_id", start=pl.col("visit_start_datetime").cast(pl.Datetime("us"))
        )
        .collect()
    )
    same_day = (
        pos.join(starts, on="subject_id")
        .filter((pl.col("start") > pl.col("prediction_time"))
                & (pl.col("start").dt.date() == pl.col("cohort_start_date")))
        .select("row_id").unique().height
    )  # fmt: skip
    print(f"readmission: {same_day} of {pos.height} positives readmitted on the discharge date")


# --- matrices, day, fit, report (e3 venv) ---


def matrices(work: Path, variant: str) -> None:
    import numpy as np
    import polars as pl
    import scipy.sparse as sp

    if variant == "fe":
        for task in TASKS:
            without_leaky(work, task)
        return
    rows = pl.read_parquet(work / "rows.parquet").select("row_id", "task", "split")
    out = work / "features" / variant
    for task in TASKS:
        if variant not in variants(task):
            continue
        source = work / "fe" / variant / task
        sizes = [pl.read_parquet(e3_features() / task / f"{s}_labels.parquet").height
                 for s in SPLITS]  # fmt: skip
        # prepare numbers a task's labels consecutively, split by split in SPLITS order, so a
        # row_id's split and position follow from its offset
        ids = rows.filter(pl.col("task") == task)["row_id"]
        base = ids.min()
        assert ids.max() - base + 1 == ids.len() == sum(sizes), task
        bounds = np.cumsum([0, *sizes])
        names = {c.lower(): c for c in pl.scan_parquet(source / "covariates.parquet")
                 .collect_schema().names()}  # fmt: skip
        cov = pl.scan_parquet(source / "covariates.parquet").select(
            row=(pl.col(names["rowid"]).cast(pl.Int64) - base).cast(pl.Int32),
            covariate=pl.col(names["covariateid"]).cast(pl.Int64),
            value=pl.col(names["covariatevalue"]).cast(pl.Float32),
        )
        ref = (
            pl.read_parquet(source / "covariate_ref.parquet")
            .rename(lambda c: c.lower())
            .with_columns(pl.col("covariateid").cast(pl.Int64), pl.col("analysisid").cast(pl.Int64))
        )
        kept = (
            cov.filter(pl.col("row") < sizes[0])
            .group_by("covariate")
            .agg(train_count=pl.len(), binary=(pl.col("value") == 1).all())
            .filter(pl.col("train_count") >= MIN_FRACTION * sizes[0])
            .collect()
            .join(ref, left_on="covariate", right_on="covariateid", how="left")
            .sort("covariate")
            .with_row_index("column")
        )
        table = kept.select(
            pl.col("column").cast(pl.Int64),
            name=pl.col("covariatename"),
            code=pl.format("{}/{}", "analysisid", "covariate"),
            agg=pl.when("binary").then(pl.lit("present")).otherwise(pl.lit("value")),
            window=pl.lit("fe"),
            train_count=pl.col("train_count").cast(pl.Int64),
        )
        # e3.load's layout, one features table per task: <out>/<task>/features.parquet and
        # <out>/<task>/<task>/<split>.npz
        target = out / task / task
        target.mkdir(parents=True, exist_ok=True)
        table.write_parquet(out / task / "features.parquet")
        entries = (
            cov.join(
                kept.lazy().select("covariate", pl.col("column").cast(pl.Int32)), on="covariate"
            )
            .select("row", "column", "value")
            .collect()
        )
        row, column, value = (entries[c].to_numpy() for c in ("row", "column", "value"))
        del entries
        for k, split in enumerate(SPLITS):
            part = (row >= bounds[k]) & (row < bounds[k + 1])
            matrix = sp.csr_matrix(
                (value[part], (row[part] - bounds[k], column[part])),
                shape=(sizes[k], table.height),
            )
            sp.save_npz(target / f"{split}.npz", matrix, compressed=False)
            shutil.copyfile(e3_features() / task / f"{split}_labels.parquet",
                            target / f"{split}_labels.parquet")  # fmt: skip
        print(f"{variant} {task}: {table.height} covariates of {ref.height}, "
              f"{len(row)} entries", flush=True)  # fmt: skip


def without_leaky(work: Path, task: str) -> None:
    """fe: fe_clean's matrices without the columns of LEAKY_ANALYSES."""
    import numpy as np
    import polars as pl
    import scipy.sparse as sp

    source, target = features_dir(work, "fe_clean", task), features_dir(work, "fe", task)
    table = pl.read_parquet(source / "features.parquet")
    analysis = table["code"].str.split("/").list.first().cast(pl.Int64)
    keep = np.flatnonzero(~analysis.is_in(LEAKY_ANALYSES).to_numpy())
    (target / task).mkdir(parents=True, exist_ok=True)
    table[keep].with_columns(column=pl.int_range(len(keep), dtype=pl.Int64)).write_parquet(
        target / "features.parquet"
    )
    for split in SPLITS:
        matrix = sp.load_npz(source / task / f"{split}.npz").tocsc()[:, keep].tocsr()
        sp.save_npz(target / task / f"{split}.npz", matrix, compressed=False)
        shutil.copyfile(source / task / f"{split}_labels.parquet",
                        target / task / f"{split}_labels.parquet")  # fmt: skip
    print(f"fe {task}: {len(keep)} of {table.height} covariates", flush=True)


def day(work: Path, workers: int) -> None:
    import featurize
    import polars as pl

    out = work / "features" / "day"
    if not (out / "done.json").exists():
        tasks = {t: work / "labels-day" / path for t, (path, _, _) in TASKS.items()}
        featurize.featurize(CDM_MEDS, tasks, out, workers=workers)
    # the predictions carry E3's own prediction times: same rows in the same order
    for task in TASKS:
        for split in SPLITS:
            mine = pl.read_parquet(out / task / f"{split}_labels.parquet")
            theirs = pl.read_parquet(e3_features() / task / f"{split}_labels.parquet")
            assert mine["subject_id"].equals(theirs["subject_id"]), (task, split)
            assert mine["boolean_value"].equals(theirs["boolean_value"]), (task, split)
            theirs.write_parquet(out / task / f"{split}_labels.parquet")
    print("day: labels restored to E3's prediction times", flush=True)


def features_dir(work: Path, variant: str, task: str) -> Path:
    return work / "features" / "day" if variant == "day" else work / "features" / variant / task


def fit(work: Path) -> None:
    jobs = []
    for task in TASKS:
        params = e3.frozen_params(e3.ROOT, task, False)
        for variant in variants(task):
            lr = "cyclops_lr" if variant == "day" else "cyclops_lr_binary"
            variance = e3.frozen_lr_variance(e3.ROOT, task, lr, False)
            for model in MODELS:
                out = work / "predictions" / variant / task / f"{model}.parquet"
                if out.exists():
                    continue
                out.parent.mkdir(parents=True, exist_ok=True)
                extra = (
                    ["--params", str(params)]
                    if model == "lightgbm"
                    else ["--variance", repr(variance)]
                )
                args = ["--features", str(features_dir(work, variant, task)), "--task", task,
                        "--model", model, "--out", str(out), *extra]  # fmt: skip
                jobs.append((f"{variant} {task} {model}", args, out.with_suffix(".log")))
    print(f"{len(jobs)} fits", flush=True)
    ok = e3._run_jobs(jobs)
    print("FIT_OK" if ok else "FIT_FAILED", flush=True)


def same_day_readmissions(work: Path):
    """Held-out readmission positives whose next admission starts later on the discharge date:
    a window ending on that date (FeatureExtraction's, and `day`'s) sees the readmission itself.
    PLP's time at risk starts the day after the index, so its readmission excludes them; the
    report scores readmission without these labels, on every side alike."""
    import polars as pl

    out = work / "readmission_same_day.parquet"
    if not out.exists():
        task = "readmission_general_hospital_30d"
        lab = pl.read_parquet(e3_features() / task / "held_out_labels.parquet").filter(
            "boolean_value"
        )
        lab = lab.select("subject_id", t=pl.col("prediction_time").cast(pl.Datetime("us")))
        starts = (
            pl.scan_parquet(CDM / "visit_occurrence/*.parquet")
            .filter(pl.col("visit_concept_id").is_in([9201, 262]))
            .select(
                subject_id="person_id", start=pl.col("visit_start_datetime").cast(pl.Datetime("us"))
            )
            .collect()
        )
        same = lab.join(starts, on="subject_id").filter(
            (pl.col("start") > pl.col("t")) & (pl.col("start").dt.date() == pl.col("t").dt.date())
        )
        same.select("subject_id", "t").unique().write_parquet(out)
    return pl.read_parquet(out)


def _compare(args):
    import compare
    import polars as pl

    task, model, a, b, what, fa, fb, exclude = args

    def read(f):
        d = pl.read_parquet(f)
        if exclude is None:
            return d
        t = pl.col("prediction_time").cast(pl.Datetime("us")).alias("t")
        return d.with_columns(t).join(exclude, on=["subject_id", "t"], how="anti").drop("t")

    r = compare.compare(read(fa), read(fb)).filter(pl.col("metric") == "auroc")
    return {"task": task, "model": model, "reference": a, "alternative": b, "measures": what,
            "auroc_reference": r["source"][0], "auroc_alternative": r["cdm"][0],
            "difference": r["difference"][0], "ci95_low": r["ci95_low"][0],
            "ci95_high": r["ci95_high"][0]}  # fmt: skip


def report(work: Path) -> None:
    import multiprocessing

    import polars as pl

    names = {(t, m): n for t, m, n, _ in e3.planned_fits(e3.ROOT, False, False)}
    f = e3.features_hash()
    main = {side: e3.side_dir(e3.ROOT, side, d) / f"predictions-{f}"
            for side, d in (("source", SOURCE_MEDS), ("cdm", CDM_MEDS))}  # fmt: skip

    def path(variant: str, task: str, model: str) -> Path:
        if variant in main:
            return main[variant] / task / names[(task, model)]
        return work / "predictions" / variant / task / f"{model}.parquet"

    same_day = same_day_readmissions(work)
    print(f"readmission scored without {same_day.height} same-day readmissions", flush=True)
    jobs = []
    for task in TASKS:
        exclude = same_day if task.startswith("readmission") else None
        for model in MODELS:
            for a, b, what in PAIRS + (("source", "cdm", "E3's CDM fit instead of the source's"),):
                have = set(variants(task)) | {"source", "cdm"}
                if a in have and b in have:
                    fa, fb = path(a, task, model), path(b, task, model)
                    jobs.append((task, model, a, b, what, fa, fb, exclude))
    # one thread per worker (BLAS/OpenMP otherwise start one per core in each); spawn, not fork:
    # a forked child inherits polars' thread pool locked and hangs
    for v in ("OMP_NUM_THREADS", "OPENBLAS_NUM_THREADS", "MKL_NUM_THREADS", "POLARS_MAX_THREADS"):
        os.environ[v] = "1"
    with multiprocessing.get_context("spawn").Pool(8) as pool:
        out = pl.DataFrame(pool.map(_compare, jobs))
    out.write_csv(work / "report.csv")
    with pl.Config(tbl_rows=200, tbl_cols=12, fmt_str_lengths=40, tbl_width_chars=250):
        print(out.select("task", "model", "reference", "alternative", "auroc_reference",
                         "auroc_alternative", "difference", "ci95_low", "ci95_high"))  # fmt: skip
    print(f"report: {work / 'report.csv'}")


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("--slots", type=int, default=2)
    p.add_argument("--threads", type=int, default=32)
    p.add_argument("--memory", default="60G")
    sub = p.add_subparsers(dest="cmd", required=True)
    for name in ("prepare", "fit", "report"):
        sub.add_parser(name).add_argument("work", type=Path)
    m = sub.add_parser("matrices")
    m.add_argument("work", type=Path)
    m.add_argument("variant", choices=("fe_clean", "fe_default", "fe"))
    d = sub.add_parser("day")
    d.add_argument("work", type=Path)
    d.add_argument("--workers", type=int, default=32)
    a = p.parse_args()
    e3.SLOTS, e3.THREADS, e3.MEMORY = a.slots, a.threads, a.memory
    if a.cmd == "prepare":
        prepare(a.work)
    elif a.cmd == "matrices":
        matrices(a.work, a.variant)
    elif a.cmd == "day":
        day(a.work, a.workers)
    elif a.cmd == "fit":
        fit(a.work)
    else:
        report(a.work)
    (a.work / f"{a.cmd}.json").write_text(json.dumps({"done": True}))


if __name__ == "__main__":  # featurize spawns workers that import this file
    main()
