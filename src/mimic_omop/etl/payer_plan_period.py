"""payer_plan_period: the insurance recorded for each admission (hosp.admissions.insurance).

MIMIC records a patient's insurance per admission, as one of Medicare, Medicaid, Private, Other
and No charge; it says nothing about coverage between admissions. So each admission with an
insurance is one period, from admission to discharge date. Payers are concepts of the Source of
Payment Typology (SOPT), through a local mapping (mappings/payer.csv): CVB maps insurance to
LOINC answers, which are not in the Payer domain. 'Other' has no SOPT equivalent and is 0.
"""

import io
from importlib.resources import files

import polars as pl

from mimic_omop.context import Inputs


def local_mapping() -> pl.LazyFrame:
    """mappings/payer.csv: insurance, sopt (a SOPT code)."""
    text = files("mimic_omop.mappings").joinpath("payer.csv").read_text()
    return pl.read_csv(io.StringIO(text), infer_schema=False).lazy()


def payer_map(mapping: pl.LazyFrame, concept: pl.LazyFrame) -> pl.LazyFrame:
    """insurance -> payer_concept_id, for SOPT codes with a valid standard concept."""
    sopt = concept.filter(
        pl.col("vocabulary_id") == "SOPT",
        pl.col("standard_concept") == "S",
        pl.col("invalid_reason").is_null(),
    ).select(sopt="concept_code", payer_concept_id="concept_id")
    return mapping.join(sopt, on="sopt").select("insurance", "payer_concept_id")


def payer_plan_period(admissions: pl.LazyFrame, payers: pl.LazyFrame) -> pl.LazyFrame:
    insurance = pl.col("insurance").str.strip_chars()
    start = pl.col("admittime").dt.date()
    return (
        admissions.filter(insurance.is_not_null() & (insurance != ""))
        .with_columns(insurance=insurance)
        .join(payers, on="insurance", how="left")
        .select(
            payer_plan_period_id="hadm_id",
            person_id="subject_id",
            payer_plan_period_start_date=start,
            payer_plan_period_end_date=pl.max_horizontal(pl.col("dischtime").dt.date(), start),
            payer_concept_id=pl.col("payer_concept_id").fill_null(0),
            payer_source_value="insurance",
            payer_source_concept_id=pl.lit(0, pl.Int64),
        )
    )


def build(inp: Inputs) -> pl.LazyFrame:
    return payer_plan_period(
        inp.src.scan("hosp/admissions"), payer_map(local_mapping(), inp.vocab.athena("concept"))
    )
