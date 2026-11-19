"""Outpatient measurements from hosp.omr (weight, height, BMI, blood pressure), dated by day.

CVB does not map omr, so this is the ETL's one local mapping: mappings/omr.csv, written as LOINC
and UCUM codes (not concept ids) and resolved through Athena at run time. A blood pressure such as
'120/80' becomes two measurements, systolic and diastolic. Result names not in the file are
written with concept 0; the file covers the result names of the MIMIC-IV demo.

omr rows belong to the visit covering their date, else to the Outpatient Visit created for that
day (see visit_occurrence).
"""

import io
from importlib.resources import files

import polars as pl

from mimic_omop import concepts as c
from mimic_omop.etl.events import SOURCE_VALUE_LENGTH, event_id, finish
from mimic_omop.etl.intervals import assign


def local_mapping() -> pl.LazyFrame:
    """mappings/omr.csv: result_name, component (value/systolic/diastolic), loinc, ucum."""
    text = files("mimic_omop.mappings").joinpath("omr.csv").read_text()
    return pl.read_csv(io.StringIO(text), infer_schema=False).lazy()


def omr_map(mapping: pl.LazyFrame, concept: pl.LazyFrame) -> pl.LazyFrame:
    """The local mapping with its codes resolved: result_name, component, k, target_concept_id,
    target_domain_id, unit_concept_id. A code without a valid standard concept resolves to null."""
    valid = concept.filter(pl.col("standard_concept") == "S", pl.col("invalid_reason").is_null())
    loinc = valid.filter(pl.col("vocabulary_id") == "LOINC").select(
        loinc="concept_code", target_concept_id="concept_id", target_domain_id="domain_id"
    )
    ucum = valid.filter(pl.col("vocabulary_id") == "UCUM").select(
        ucum="concept_code", unit_concept_id="concept_id"
    )
    return (
        mapping.with_columns(k=pl.int_range(pl.len()).over("result_name"))
        .join(loinc, on="loinc", how="left")
        .join(ucum, on="ucum", how="left")
        .drop("loinc", "ucum")
    )


def omr_events(omr: pl.LazyFrame, visits: pl.LazyFrame, mapping: pl.LazyFrame) -> pl.LazyFrame:
    lf = (
        omr.rename({"subject_id": "person_id"})
        .with_columns(
            result_name=pl.col("result_name").str.strip_chars(),
            seq=pl.int_range(pl.len()).over(
                "person_id", order_by=["chartdate", "seq_num", "result_name"]
            ),
        )
        .join(mapping, on="result_name", how="left")
        .with_columns(k=pl.col("k").fill_null(0))
    )
    parts = pl.col("result_value").str.split("/")
    value = (
        pl.when(pl.col("component") == "systolic")
        .then(parts.list.get(0, null_on_oob=True))
        .when(pl.col("component") == "diastolic")
        .then(parts.list.get(1, null_on_oob=True))
        .otherwise(pl.col("result_value"))
    )
    lf = assign(
        lf,
        visits.select("person_id", "visit_occurrence_id", "visit_start_date", "visit_end_date"),
        by="person_id",
        time="chartdate",
        start="visit_start_date",
        end="visit_end_date",
        interval_id="visit_occurrence_id",
        out="visit",
    )
    return finish(
        lf,
        event_id=event_id("omr", pl.col("person_id") * 10**6 + pl.col("seq")),
        person_id="person_id",
        visit_occurrence_id="visit",
        start_date="chartdate",
        type_concept_id=pl.lit(c.EHR),
        concept_id=pl.col("target_concept_id").fill_null(0),
        domain_id=pl.col("target_domain_id").fill_null("Measurement"),
        source_value=pl.col("result_name").str.slice(0, SOURCE_VALUE_LENGTH),
        source_concept_id=pl.lit(0),
        value_as_number=value.str.strip_chars().cast(pl.Float64, strict=False),
        unit_concept_id="unit_concept_id",
        value_source_value=pl.col("result_value").str.slice(0, SOURCE_VALUE_LENGTH),
    )
