"""The CDM in MEDS, for evaluation E3 (docs/evaluation.md, P4).

The published OMOP converters do not fit the comparison: meds_etl and OMOP_MEDS accept CDM 5.3
and 5.4 only, meds_etl codes events by source concept, and neither emits ICU stays. This one
reads the CDM parquet and makes each choice explicit:

- codes: `standard` (main) is the standard concept as `vocabulary_id/concept_code`, else the
  source concept, else `<table>//<source value>`, so no record is dropped; `source` puts the
  source concept first, as meds_etl does. Measurement codes carry their unit
  (`LOINC/2160-0//mg/dL`), as the source MEDS's lab codes do;
- times: the record's datetime, else the end of its day (a date-only record cannot be known
  before the day ends);
- diagnoses: `end` (main) places billing diagnoses (type 32821 with an ICD-9-CM or ICD-10-CM
  source concept) at their visit's end, when they are known, as the source MEDS dates
  diagnoses_icd at discharge; `start` keeps the CDM's date (the visit start), to measure the
  leak that gives a model predicting within an admission;
- events: MEDS_BIRTH, sex, race, ethnicity at birth; MEDS_DEATH; `VISIT//<concept>` at each
  visit's start and `VISIT_END//<concept>` at its end; `ADMITTED_FROM//` and `DISCHARGED_TO//`
  with the visit's place concept (else its source value) at the same times;
  `VISIT_DETAIL//<concept>` at each care unit stay's start; `ICU_ADMISSION` and `ICU_DISCHARGE`
  for each ICU stay, consecutive ICU care unit stays of one visit merged as MIMIC's icustays
  merges them; the records of the clinical event tables;
- `sources`: if given, only records whose event id prefix (the ETL's source table, see
  docs/general.md) is listed, plus the person, visit and death events (the input-matched variant).

Subjects keep the split and shard file they have in the source MEDS, so the two sides pair file
by file.
"""

import json
import shutil
from datetime import UTC, datetime
from pathlib import Path

import polars as pl

BILLING = 32821
ICU = 32037
DIAGNOSIS_VOCABULARIES = ("ICD9CM", "ICD10CM")
END_OF_DAY = pl.duration(days=1) - pl.duration(microseconds=1)
MEDS = {
    "subject_id": pl.Int64,
    "time": pl.Datetime("us"),
    "code": pl.String,
    "numeric_value": pl.Float32,
    "text_value": pl.String,
}
# clinical event tables: (id, concept, source concept, source value, date, datetime, type)
TABLES = {
    "condition_occurrence": (
        "condition_occurrence_id",
        "condition_concept_id",
        "condition_source_concept_id",
        "condition_source_value",
        "condition_start_date",
        "condition_start_datetime",
        "condition_type_concept_id",
    ),
    "procedure_occurrence": (
        "procedure_occurrence_id",
        "procedure_concept_id",
        "procedure_source_concept_id",
        "procedure_source_value",
        "procedure_date",
        "procedure_datetime",
        "procedure_type_concept_id",
    ),
    "drug_exposure": (
        "drug_exposure_id",
        "drug_concept_id",
        "drug_source_concept_id",
        "drug_source_value",
        "drug_exposure_start_date",
        "drug_exposure_start_datetime",
        "drug_type_concept_id",
    ),
    "measurement": (
        "measurement_id",
        "measurement_concept_id",
        "measurement_source_concept_id",
        "measurement_source_value",
        "measurement_date",
        "measurement_datetime",
        "measurement_type_concept_id",
    ),
    "observation": (
        "observation_id",
        "observation_concept_id",
        "observation_source_concept_id",
        "observation_source_value",
        "observation_date",
        "observation_datetime",
        "observation_type_concept_id",
    ),
    "device_exposure": (
        "device_exposure_id",
        "device_concept_id",
        "device_source_concept_id",
        "device_source_value",
        "device_exposure_start_date",
        "device_exposure_start_datetime",
        "device_type_concept_id",
    ),
}


def _scan(cdm: Path, table: str) -> pl.LazyFrame:
    return pl.scan_parquet(Path(cdm) / table / "*.parquet")


def _time(date: str, dt: str) -> pl.Expr:
    return pl.coalesce(pl.col(dt), pl.col(date).cast(pl.Datetime("us")) + END_OF_DAY)


def concept_codes(cdm: Path) -> pl.LazyFrame:
    """concept_id -> `vocabulary_id/concept_code`, and the vocabulary, from the CDM's concept
    table (Athena and CVB's MIMIC4 concepts)."""
    return _scan(cdm, "concept").select(
        "concept_id",
        concept_code=pl.col("vocabulary_id") + "/" + pl.col("concept_code"),
        vocabulary_id="vocabulary_id",
    )


def _code(concepts: pl.LazyFrame, lf: pl.LazyFrame, column: str, alias: str) -> pl.LazyFrame:
    c = concepts.select(
        pl.col("concept_id").alias(column),
        pl.col("concept_code").alias(alias),
        pl.col("vocabulary_id").alias(f"{alias}_vocabulary"),
    )
    return lf.join(c, on=column, how="left")


def clinical_events(
    cdm: Path, concepts: pl.LazyFrame, table: str, codes: str, diagnoses: str
) -> pl.LazyFrame:
    id_, concept, source, value, date, dt, type_ = TABLES[table]
    lf = _scan(cdm, table).with_columns(
        concept_id=pl.when(pl.col(concept) != 0).then(concept),
        source_id=pl.when(pl.col(source).fill_null(0) != 0).then(source),
    )
    lf = _code(concepts, _code(concepts, lf, "concept_id", "std"), "source_id", "src")
    fallback = pl.lit(f"{table}//") + pl.col(value).fill_null("")
    first, second = ("std", "src") if codes == "standard" else ("src", "std")
    code = pl.coalesce(first, second, fallback)
    numeric, text = pl.lit(None, pl.Float64), pl.lit(None, pl.String)
    if table == "measurement":
        units = concepts.select(unit_concept_id="concept_id", unit=pl.col("concept_code"))
        lf = lf.join(units, on="unit_concept_id", how="left")
        unit = pl.coalesce(
            pl.col("unit").str.split("/").list.slice(1).list.join("/"), "unit_source_value"
        )
        code = pl.when(unit.is_not_null()).then(code + "//" + unit).otherwise(code)
        numeric = pl.col("value_as_number")
        text = pl.when(pl.col("value_as_number").is_null()).then("value_source_value")
    elif table == "observation":
        numeric = pl.col("value_as_number")
        text = pl.coalesce(
            "value_as_string",
            pl.when(pl.col("value_as_number").is_null()).then("value_source_value"),
        )
    time = _time(date, dt)
    if diagnoses == "end":
        visits = _scan(cdm, "visit_occurrence").select(
            "visit_occurrence_id", visit_end=_time("visit_end_date", "visit_end_datetime")
        )
        lf = lf.join(visits, on="visit_occurrence_id", how="left")
        billed = (pl.col(type_) == BILLING) & pl.col("src_vocabulary").is_in(DIAGNOSIS_VOCABULARIES)
        time = pl.when(billed & pl.col("visit_end").is_not_null()).then("visit_end").otherwise(time)
    return lf.select(
        subject_id="person_id",
        time=time,
        code=code,
        numeric_value=numeric,
        text_value=text,
        source=pl.col(id_) // 10**15,
    )


def icu_stays(details: pl.LazyFrame) -> pl.LazyFrame:
    """ICU stays: consecutive ICU care unit stays of one visit merged (person_id, start, end)."""
    icu = details.filter(pl.col("visit_detail_concept_id") == ICU).select(
        "person_id",
        "visit_occurrence_id",
        start="visit_detail_start_datetime",
        end="visit_detail_end_datetime",
    )
    prior_end = pl.col("end").cum_max().shift(1).over("visit_occurrence_id", order_by="start")
    return (
        icu.with_columns(new=(prior_end.is_null() | (pl.col("start") > prior_end)).cast(pl.Int64))
        .with_columns(stay=pl.col("new").cum_sum().over("visit_occurrence_id", order_by="start"))
        .group_by("person_id", "visit_occurrence_id", "stay")
        .agg(pl.col("start").min(), pl.col("end").max())
        .select("person_id", "start", "end")
    )


def structural_events(cdm: Path, concepts: pl.LazyFrame) -> pl.LazyFrame:
    """Birth, demographics, death, visits, care unit stays and ICU stays."""

    def code(column: str, prefix: str = "") -> pl.LazyFrame:
        return concepts.select(
            pl.col("concept_id").alias(column), pl.lit(prefix) + pl.col("concept_code")
        )

    def ev(lf: pl.LazyFrame, time: pl.Expr, code: pl.Expr) -> pl.LazyFrame:
        return lf.select(
            subject_id="person_id",
            time=time,
            code=code,
            numeric_value=pl.lit(None, pl.Float64),
            text_value=pl.lit(None, pl.String),
            source=pl.lit(None, pl.Int64),
        )

    person = _scan(cdm, "person").with_columns(
        birth=pl.coalesce(
            "birth_datetime",
            pl.datetime(
                "year_of_birth",
                pl.col("month_of_birth").fill_null(1),
                pl.col("day_of_birth").fill_null(1),
            ),
        )
    )
    parts = [ev(person, pl.col("birth"), pl.lit("MEDS_BIRTH"))]
    for column in ("gender_concept_id", "race_concept_id", "ethnicity_concept_id"):
        c = concepts.select(pl.col("concept_id").alias(column), code="concept_code")
        parts.append(ev(person.join(c, on=column), pl.col("birth"), pl.col("code")))
    death = _scan(cdm, "death")
    parts.append(ev(death, _time("death_date", "death_datetime"), pl.lit("MEDS_DEATH")))
    c = concepts.select(visit_concept_id="concept_id", code="concept_code")
    visits = _scan(cdm, "visit_occurrence").join(c, on="visit_concept_id")
    parts.append(
        ev(visits, _time("visit_start_date", "visit_start_datetime"), "VISIT//" + pl.col("code"))
    )
    parts.append(
        ev(visits, _time("visit_end_date", "visit_end_datetime"), "VISIT_END//" + pl.col("code"))
    )
    # where the patient came from and went to (the source MEDS's admission and discharge
    # locations): the concept, else the source value
    for column, prefix, time in (
        ("admitted_from", "ADMITTED_FROM//", _time("visit_start_date", "visit_start_datetime")),
        ("discharged_to", "DISCHARGED_TO//", _time("visit_end_date", "visit_end_datetime")),
    ):
        concept = f"{column}_concept_id"
        c = concepts.filter(pl.col("concept_id") != 0).select(
            pl.col("concept_id").alias(concept), place="concept_code"
        )
        place = visits.join(c, on=concept, how="left").with_columns(
            place=pl.coalesce("place", f"{column}_source_value")
        )
        place = place.filter(pl.col("place").is_not_null())
        parts.append(ev(place, time, prefix + pl.col("place")))
    c = concepts.select(visit_detail_concept_id="concept_id", code="concept_code")
    details = _scan(cdm, "visit_detail")
    parts.append(
        ev(
            details.join(c, on="visit_detail_concept_id"),
            pl.col("visit_detail_start_datetime"),
            "VISIT_DETAIL//" + pl.col("code"),
        )
    )
    stays = icu_stays(details)
    parts.append(ev(stays, pl.col("start"), pl.lit("ICU_ADMISSION")))
    parts.append(ev(stays, pl.col("end"), pl.lit("ICU_DISCHARGE")))
    return pl.concat(parts, how="vertical_relaxed")


def shard_map(source_meds: Path) -> pl.DataFrame:
    """subject_id -> split, shard, as in the source MEDS (data/<split>/<shard>.parquet)."""
    rows = []
    for f in sorted(Path(source_meds, "data").glob("*/*.parquet")):
        ids = pl.scan_parquet(f).select(pl.col("subject_id").unique()).collect()
        rows.append(ids.with_columns(split=pl.lit(f.parent.name), shard=pl.lit(int(f.stem))))
    return pl.concat(rows)


def convert(
    cdm: Path,
    source_meds: Path,
    out: Path,
    *,
    codes: str = "standard",
    diagnoses: str = "end",
    sources: list[int] | None = None,
) -> dict[str, int]:
    """Write the CDM as MEDS to `out` (data/, metadata/); returns events per table."""
    if codes not in ("standard", "source") or diagnoses not in ("end", "start"):
        raise ValueError(f"codes={codes}, diagnoses={diagnoses}")
    out = Path(out)
    shutil.rmtree(out, ignore_errors=True)
    staging = out / ".staging"
    concepts = concept_codes(cdm)
    shards = shard_map(source_meds)
    counts = {}
    parts = {"structural": structural_events(cdm, concepts)}
    parts |= {t: clinical_events(cdm, concepts, t, codes, diagnoses) for t in TABLES}
    for name, lf in parts.items():
        if sources is not None and name != "structural":
            lf = lf.filter(pl.col("source").is_in(sources))
        lf = lf.drop("source").join(shards.lazy(), on="subject_id")
        lf.sink_parquet(
            pl.PartitionBy(staging / name, key=["split", "shard"], include_key=False), mkdir=True
        )
        counts[name] = (
            pl.scan_parquet(staging / name / "**/*.parquet").select(pl.len()).collect().item()
        )
    for (split, shard), _ in shards.group_by("split", "shard"):
        files = sorted(staging.glob(f"*/split={split}/shard={shard}/*.parquet"))
        df = (
            pl.scan_parquet(files)
            .select([pl.col(c).cast(t) for c, t in MEDS.items()])
            .sort("subject_id", "time", "code", nulls_last=False)
            .collect()
        )
        target = out / "data" / split / f"{shard}.parquet"
        target.parent.mkdir(parents=True, exist_ok=True)
        df.write_parquet(target)
    shutil.rmtree(staging)
    (out / "metadata").mkdir(parents=True, exist_ok=True)
    shards.select("subject_id", "split").write_parquet(out / "metadata/subject_splits.parquet")
    names = _scan(cdm, "concept").select(
        code=pl.col("vocabulary_id") + "/" + pl.col("concept_code"), description="concept_name"
    )
    used = pl.scan_parquet(out / "data/*/*.parquet").select("code").unique()
    used.join(names, on="code", how="left").with_columns(
        parent_codes=pl.lit(None, pl.List(pl.String))
    ).collect().write_parquet(out / "metadata/codes.parquet")
    (out / "metadata/dataset.json").write_text(
        json.dumps(
            {
                "dataset_name": "MIMIC-IV OMOP CDM",
                "dataset_version": f"codes={codes};diagnoses={diagnoses};sources={sources}",
                "etl_name": "mimic_eval.omop_meds",
                "etl_version": "0.1.0",
                "meds_version": "0.4.1",
                "created_at": datetime.now(UTC).isoformat(),
            }
        )
    )
    return counts
