"""MIMIC-shaped tables rebuilt from the CDM, for running mimic-code's SQL on it (evaluation E2a).

Each table is built only from what the CDM records, as an analyst of the CDM would find it:

- admissions: the visits that are admissions (Inpatient 9201, Inpatient Visit 262 and
  Observation Room 581385), `hadm_id` = visit_occurrence_id;
- patients: `anchor_year` = year_of_birth and `anchor_age` = 0, so mimic-code's age
  (anchor_age + years from anchor_year to admission) is the age the CDM gives;
- diagnoses_icd: records of any event table whose source concept is an ICD-9-CM or ICD-10-CM
  code, the code from `*_source_value` and the version from the source concept's vocabulary;
  or, for the standard-concept variant, one representative ICD code per Charlson component
  found by standard concept (`charlson.py`);
- labevents and chartevents: measurement records selected by standard concept and unit (see
  `item_keys`), whatever source table they came from. MIMIC's lab `specimen_id`, which the CDM
  does not keep, is replaced by one id per person and time.

ICU stays are the exception: `icustays` is taken from the source, so both sides are compared on
the same stays and time windows. A chart record gets the `stay_id` of the ICU stay its
`visit_detail_id` (the care unit stay the CDM attaches it to) is part of, i.e. a care unit stay
overlapping the ICU stay's times; a record whose visit detail is not part of an ICU stay has no
`stay_id`, and mimic-code's chart concepts leave it out.
"""

from pathlib import Path

import polars as pl

ADMISSION_VISITS = (9201, 262, 581385)
ICD_VOCABULARIES = {"ICD9CM": 9, "ICD10CM": 10}
# event table -> (visit field, source value, source concept, standard concept)
EVENT_TABLES = {
    t: ("visit_occurrence_id", f"{d}_source_value", f"{d}_source_concept_id", f"{d}_concept_id")
    for t, d in {
        "condition_occurrence": "condition",
        "observation": "observation",
        "procedure_occurrence": "procedure",
        "measurement": "measurement",
        "device_exposure": "device",
        "drug_exposure": "drug",
    }.items()
}


def scan(cdm: Path, table: str) -> pl.LazyFrame:
    return pl.scan_parquet(Path(cdm) / table / "part-*.parquet")


def admissions(cdm: Path) -> pl.LazyFrame:
    return (
        scan(cdm, "visit_occurrence")
        .filter(pl.col("visit_concept_id").is_in(ADMISSION_VISITS))
        .select(
            subject_id="person_id",
            hadm_id="visit_occurrence_id",
            admittime="visit_start_datetime",
            dischtime="visit_end_datetime",
        )
    )


def patients(cdm: Path) -> pl.LazyFrame:
    gender = pl.col("gender_concept_id").replace_strict({8507: "M", 8532: "F"}, default=None)
    return scan(cdm, "person").select(
        subject_id="person_id",
        gender=gender,
        anchor_age=pl.lit(0, pl.Int64),
        anchor_year=pl.col("year_of_birth").cast(pl.Int64),
    )


def diagnoses_icd(
    cdm: Path, vocab: Path, tables: tuple[str, ...] = tuple(EVENT_TABLES), ed: bool = False
) -> pl.LazyFrame:
    """Records with an ICD-CM source concept, in MIMIC's diagnoses_icd shape (seq_num is not
    known and not used by mimic-code). ed False leaves out MIMIC-IV-ED's diagnoses, which mimic-code
    does not read: they are the only ICD-coded records with a care unit stay (hospital diagnoses
    have no visit_detail_id)."""
    icd = (
        pl.scan_parquet(Path(vocab) / "athena/concept.parquet")
        .filter(pl.col("vocabulary_id").is_in(list(ICD_VOCABULARIES)))
        .select(
            source_concept_id="concept_id",
            icd_version=pl.col("vocabulary_id").replace_strict(ICD_VOCABULARIES),
        )
    )
    parts = [
        scan(cdm, t)
        .select(
            "person_id",
            pl.col(v).alias("hadm_id"),
            pl.col(s).alias("icd_code"),
            pl.col(sc).alias("source_concept_id"),
            "visit_detail_id",
        )
        .filter(pl.lit(ed) | pl.col("visit_detail_id").is_null())
        .join(icd, on="source_concept_id")
        for t, (v, s, sc, _) in EVENT_TABLES.items()
        if t in tables
    ]
    return pl.concat(parts).select(
        subject_id="person_id",
        hadm_id="hadm_id",
        seq_num=pl.lit(None, pl.Int64),
        icd_code="icd_code",
        icd_version=pl.col("icd_version").cast(pl.Int64),
    )


def item_keys(cdm: Path, itemids: list[int]) -> pl.DataFrame:
    """The (standard concept, unit) of each mimic-code item, and the item each such pair is read
    back as.

    The concept and unit an analyst would select for an item are those the CDM gives the item's
    records (found once through their source value). A pair used by several of the items is read
    back as the item with the most records (`items` lists them all), so those items cannot be
    told apart on the CDM. Records with concept 0 cannot be selected and give no pair.
    """
    keys = (
        scan(cdm, "measurement")
        .filter(pl.col("measurement_source_value").is_in([str(i) for i in itemids]))
        .filter(pl.col("measurement_concept_id") != 0)
        .group_by(
            itemid=pl.col("measurement_source_value").cast(pl.Int64),
            concept_id="measurement_concept_id",
            unit_concept_id="unit_concept_id",
        )
        .agg(n=pl.len())
        .collect()
    )
    return (
        keys.sort("n", descending=True)
        .group_by("concept_id", "unit_concept_id", maintain_order=True)
        .agg(
            itemid=pl.col("itemid").first(),
            items=pl.col("itemid").unique().sort(),
            n=pl.col("n").sum(),
        )
    )


def _measurements(cdm: Path, keys: pl.DataFrame, ed: bool = False) -> pl.LazyFrame:
    """Measurements of the (concept, unit) keys. ed False leaves out MIMIC-IV-ED's triage and
    vital signs, which mimic-code does not read: their source value is the ED column name
    (`sbp`), not an item id."""
    return (
        scan(cdm, "measurement")
        .filter(pl.lit(ed) | pl.col("measurement_source_value").str.contains(r"^\d+$"))
        .join(
            keys.lazy().select("concept_id", "unit_concept_id", "itemid"),
            left_on=["measurement_concept_id", "unit_concept_id"],
            right_on=["concept_id", "unit_concept_id"],
            nulls_equal=True,
        )
        .select(
            subject_id="person_id",
            hadm_id="visit_occurrence_id",
            itemid="itemid",
            charttime="measurement_datetime",
            value="value_source_value",
            valuenum="value_as_number",
            valueuom="unit_source_value",
            visit_detail_id="visit_detail_id",
        )
    )


def labevents(cdm: Path, keys: pl.DataFrame) -> pl.LazyFrame:
    specimen = pl.struct("subject_id", "charttime").hash().reinterpret(signed=True)
    return (
        _measurements(cdm, keys.filter(pl.col("itemid") < 200_000))
        .with_columns(specimen_id=specimen)
        .drop("visit_detail_id")
    )


def stay_of_detail(cdm: Path, icustays: pl.LazyFrame) -> pl.LazyFrame:
    """visit_detail_id -> stay_id: the ICU stay a care unit stay overlaps (the earliest, if two)."""
    details = scan(cdm, "visit_detail").select(
        "visit_detail_id",
        hadm_id="visit_occurrence_id",
        start="visit_detail_start_datetime",
        end="visit_detail_end_datetime",
    )
    return (
        icustays.select("stay_id", "hadm_id", "intime", "outtime")
        .join(details, on="hadm_id")
        .filter(pl.col("start") < pl.col("outtime"), pl.col("end") > pl.col("intime"))
        .sort("intime")
        .unique("visit_detail_id", keep="first")
        .select("visit_detail_id", "stay_id")
    )


def chartevents(
    cdm: Path, keys: pl.DataFrame, icustays: pl.LazyFrame, ed: bool = False
) -> pl.LazyFrame:
    return (
        _measurements(cdm, keys.filter(pl.col("itemid") >= 200_000), ed)
        .join(stay_of_detail(cdm, icustays), on="visit_detail_id", how="left")
        .drop("visit_detail_id")
    )
