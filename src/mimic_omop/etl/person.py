"""person from hosp.patients, with race/ethnicity from hosp.admissions.

Conventions:
- person_id = subject_id.
- year_of_birth = anchor_year - anchor_age (dates are shifted; ages over 89 are capped at 91).
- Persons without an observation period, i.e. without any clinical event, are excluded: the CDM
  requires every person to have one, and THEMIS allows documented exclusions
  (https://ohdsi.github.io/Themis/excluded_persons.html).
- race comes from the most recent admission with an informative race string. MIMIC stores race and
  ethnicity in one string; CVB maps some of them to ethnicity concepts (e.g. 'BLACK/CAPE VERDEAN'),
  in which case race falls back to the string's prefix.
- ethnicity is the OMB category, derived from the same string.
"""

import polars as pl

from mimic_omop import concepts as c
from mimic_omop.context import Inputs

UNINFORMATIVE_RACE = ("UNKNOWN", "UNABLE TO OBTAIN", "PATIENT DECLINED TO ANSWER", "OTHER")
UNKNOWN_ETHNICITY = (*UNINFORMATIVE_RACE, "MULTIPLE RACE/ETHNICITY")
RACE_PREFIX = {"WHITE": c.WHITE, "BLACK": c.BLACK, "ASIAN": c.ASIAN}


def year_of_birth() -> pl.Expr:
    return pl.col("anchor_year") - pl.col("anchor_age")


def person(
    patients: pl.LazyFrame,
    admissions: pl.LazyFrame,
    race_map: pl.LazyFrame,
    observed: pl.LazyFrame | None = None,
) -> pl.LazyFrame:
    """observed: person_id of persons with an observation period; others are left out."""
    if observed is not None:
        patients = patients.join(
            observed.select(subject_id="person_id").unique(), on="subject_id", how="semi"
        )
    race = (
        admissions.filter(pl.col("race").is_not_null())
        .select("subject_id", "hadm_id", "admittime", "race")
        .join(
            race_map.filter(pl.col("target_domain_id") == "Race").select(
                race="source_code",
                race_concept_id="target_concept_id",
                race_source_concept_id="source_concept_id",
            ),
            on="race",
            how="left",
        )
        .with_columns(informative=~pl.col("race").is_in(UNINFORMATIVE_RACE))
        .group_by("subject_id")
        # admissions can share an admittime (4,774 subjects): hadm_id makes the pick deterministic
        .agg(
            pl.all()
            .sort_by("informative", "admittime", "hadm_id", descending=[True, True, True])
            .first()
        )
    )
    race_concept = pl.coalesce(
        "race_concept_id",
        pl.col("race")
        .str.extract(r"^(WHITE|BLACK|ASIAN)")
        .replace_strict(RACE_PREFIX, default=None, return_dtype=pl.Int64),
        pl.lit(0),
    )
    ethnicity = (
        pl.when(pl.col("race").str.starts_with("HISPANIC") | (pl.col("race") == "SOUTH AMERICAN"))
        .then(c.HISPANIC)
        .when(pl.col("race").is_null() | pl.col("race").is_in(UNKNOWN_ETHNICITY))
        .then(0)
        .otherwise(c.NOT_HISPANIC)
    )
    return patients.join(race, on="subject_id", how="left").select(
        person_id="subject_id",
        gender_concept_id=pl.col("gender").replace_strict(
            {"M": c.MALE, "F": c.FEMALE}, default=0, return_dtype=pl.Int64
        ),
        year_of_birth=year_of_birth(),
        race_concept_id=race_concept,
        ethnicity_concept_id=ethnicity,
        person_source_value=pl.col("subject_id").cast(pl.String),
        gender_source_value="gender",
        gender_source_concept_id=pl.lit(0),
        race_source_value="race",
        race_source_concept_id=pl.col("race_source_concept_id").fill_null(0),
        ethnicity_source_value="race",
        ethnicity_source_concept_id=pl.lit(0),
    )


def build(inp: Inputs) -> pl.LazyFrame:
    return person(
        inp.src.scan("hosp/patients"),
        inp.src.scan("hosp/admissions"),
        inp.vocab.lookup("mimiciv_hosp.admissions.race"),
        inp.cdm.scan("observation_period"),
    )
