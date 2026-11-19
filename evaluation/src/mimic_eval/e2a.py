"""E2a: mimic-code concepts on the source and on the CDM (docs/evaluation.md).

Runs, each into out/<side>/:
- source: mimic-code on the converted MIMIC-IV parquet;
- cdm: the same SQL on tables rebuilt from the CDM (`cdm_tables`): Charlson from diagnosis
  source values in every event table, labs and vital signs by standard concept and unit; like
  for like, without the MIMIC-IV-ED records that mimic-code does not read (told apart by CDM
  fields, see cdm_tables);
- cdm_with_ed: Charlson and vital signs with them, as an analyst of the CDM would get them;
- cdm_conditions: Charlson from condition_occurrence only, as an analyst looking only there;
- cdm_standard_{mapped,specific,descendants}: Charlson from standard concepts, with three
  ways of building the concept sets (`charlson.concept_sets`).

Then compares them (out/*.csv). KDIGO stages are computed from kdigo_creatinine with
mimic-code's creatinine criteria (kdigo_stages.sql, aki_stage_creat), on both sides alike.
"""

import logging
from pathlib import Path

import polars as pl

from mimic_eval import cdm_tables, charlson, compare, mimic_code

log = logging.getLogger(__name__)

KDIGO_CREATININE = ["creat", "creat_low_past_48hr", "creat_low_past_7day"]


def aki_stage_creat() -> pl.Expr:
    """kdigo_stages.sql, cr_stg (nulls compare false, as in SQL)."""
    cr, low7, low48 = pl.col("creat"), pl.col("creat_low_past_7day"), pl.col("creat_low_past_48hr")
    return (
        pl.when((cr >= low7 * 3.0).fill_null(False))
        .then(3)
        .when(
            (cr >= 4) & ((cr >= low48 + 0.3).fill_null(False) | (cr >= 1.5 * low7).fill_null(False))
        )
        .then(3)
        .when((cr >= low7 * 2.0).fill_null(False))
        .then(2)
        .when((cr >= low48 + 0.3).fill_null(False))
        .then(1)
        .when((cr >= low7 * 1.5).fill_null(False))
        .then(1)
        .otherwise(0)
    )


def item_ids(mimic_code_dir: Path) -> list[int]:
    """Lab and chart item ids used by the lab and vital sign SQL."""
    import re

    ids = set()
    for name, f in mimic_code.sql_files(mimic_code_dir, mimic_code.LABS, mimic_code.VITALS).items():
        if name.startswith("first_day"):
            continue
        ids |= {int(m) for m in re.findall(r"\b(5\d{4}|2\d{5})\b", f.read_text())}
    return sorted(ids)


def run(
    source: Path, cdm: Path, vocab: Path, mimic_code_dir: Path, out: Path
) -> dict[str, pl.DataFrame]:
    out = Path(out)
    sql = lambda *g: mimic_code.sql_files(mimic_code_dir, *g)  # noqa: E731
    src = mimic_code.source_tables(source)
    icustays = pl.scan_parquet(src["mimiciv_icu.icustays"])

    log.info("source side")
    s = mimic_code.run(
        src, sql(mimic_code.CHARLSON, mimic_code.LABS, mimic_code.VITALS), out / "source"
    )

    log.info("cdm side")
    keys = cdm_tables.item_keys(cdm, item_ids(mimic_code_dir))
    keys.with_columns(pl.col("items").cast(pl.List(pl.String)).list.join(",")).write_csv(
        out / "item_keys.csv"
    )
    common = {
        "mimiciv_hosp.admissions": cdm_tables.admissions(cdm),
        "mimiciv_hosp.patients": cdm_tables.patients(cdm),
        "mimiciv_icu.icustays": src["mimiciv_icu.icustays"],
    }
    c = mimic_code.run(
        {
            **common,
            "mimiciv_hosp.diagnoses_icd": cdm_tables.diagnoses_icd(cdm, vocab),
            "mimiciv_hosp.labevents": cdm_tables.labevents(cdm, keys),
            "mimiciv_icu.chartevents": cdm_tables.chartevents(cdm, keys, icustays),
        },
        sql(mimic_code.CHARLSON, mimic_code.LABS, mimic_code.VITALS),
        out / "cdm",
    )
    charlson_runs = {"cdm": c["charlson"]}
    with_ed = mimic_code.run(
        {
            **common,
            "mimiciv_hosp.diagnoses_icd": cdm_tables.diagnoses_icd(cdm, vocab, ed=True),
            "mimiciv_icu.chartevents": cdm_tables.chartevents(cdm, keys, icustays, ed=True),
        },
        sql(mimic_code.CHARLSON, mimic_code.VITALS),
        out / "cdm_with_ed",
    )
    charlson_runs["cdm_with_ed"] = with_ed["charlson"]
    cond = mimic_code.run(
        {
            **common,
            "mimiciv_hosp.diagnoses_icd": cdm_tables.diagnoses_icd(
                cdm, vocab, ("condition_occurrence",)
            ),
        },
        sql(mimic_code.CHARLSON),
        out / "cdm_conditions",
    )
    charlson_runs["cdm_conditions"] = cond["charlson"]

    log.info("charlson by standard concept")
    components = charlson.code_components(vocab, mimic_code_dir, out / "icd_components")
    components.write_parquet(out / "icd_components.parquet")
    reps = charlson.representatives(components)
    for kind in charlson.SET_KINDS:
        name = f"cdm_standard_{kind}"
        sets = charlson.concept_sets(components, vocab, kind)
        sets.write_parquet(out / f"{name}_concept_sets.parquet")
        r = mimic_code.run(
            {
                **common,
                "mimiciv_hosp.diagnoses_icd": charlson.diagnoses_by_concept(cdm, sets, reps),
            },
            sql(mimic_code.CHARLSON),
            out / name,
        )
        charlson_runs[name] = r["charlson"]
    return summarise(s, c, charlson_runs, out, with_ed)


def summarise(
    s: dict[str, Path],
    c: dict[str, Path],
    charlson_runs: dict[str, Path],
    out: Path,
    with_ed: dict[str, Path] | None = None,
) -> dict[str, pl.DataFrame]:
    results = {}
    src_ch = pl.read_parquet(s["charlson"])
    scored = ["age_score", *charlson.COMPONENTS, "charlson_comorbidity_index"]
    results["charlson"] = pl.concat(
        [
            compare.flags(src_ch, pl.read_parquet(p), ["hadm_id"], scored).with_columns(
                variant=pl.lit(v)
            )
            for v, p in charlson_runs.items()
        ]
    ).select("variant", pl.exclude("variant"))

    first_day = [("first_day_lab", c), ("first_day_vitalsign", c)]
    if with_ed is not None:
        first_day.append(("first_day_vitalsign", with_ed))
    for table, side in first_day:
        a, b = pl.read_parquet(s[table]), pl.read_parquet(side[table])
        cols = [x for x in a.columns if x not in ("subject_id", "stay_id")]
        name = table if side is c else f"{table}_with_ed"
        results[name] = compare.values(
            a.drop("subject_id"), b.drop("subject_id"), ["stay_id"], cols
        )

    kd = {}
    for side, paths in (("source", s), ("cdm", c)):
        kd[side] = pl.read_parquet(paths["kdigo_creatinine"]).filter(
            pl.col("charttime").is_not_null()
        )
    results["kdigo_creatinine"] = compare.values(
        kd["source"].drop("hadm_id"),
        kd["cdm"].drop("hadm_id"),
        ["stay_id", "charttime"],
        KDIGO_CREATININE,
    )
    stages = {
        side: df.with_columns(stage=aki_stage_creat())
        .group_by("stay_id")
        .agg(max_stage=pl.col("stage").max())
        for side, df in kd.items()
    }
    results["kdigo_stage"] = compare.flags(
        stages["source"], stages["cdm"], ["stay_id"], ["max_stage"]
    )
    for name, df in results.items():
        df.write_csv(out / f"{name}.csv")
    return results
