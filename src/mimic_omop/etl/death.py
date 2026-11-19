"""death from hosp.admissions.deathtime (in-hospital) and hosp.patients.dod.

dod includes out-of-hospital deaths from state records up to one year after the last discharge,
so it must be used: the in-hospital deathtime alone misses most deaths after discharge.
In-hospital deaths keep their timestamp and type EHR; dod-only deaths are typed Registry.
"""

import polars as pl

from mimic_omop import concepts as c
from mimic_omop.context import Inputs


def death(patients: pl.LazyFrame, admissions: pl.LazyFrame) -> pl.LazyFrame:
    in_hospital = (
        admissions.filter(pl.col("deathtime").is_not_null())
        .group_by("subject_id")
        .agg(pl.col("deathtime").min())
    )
    return (
        patients.select("subject_id", "dod")
        .join(in_hospital, on="subject_id", how="left")
        .filter(pl.col("deathtime").is_not_null() | pl.col("dod").is_not_null())
        .select(
            person_id="subject_id",
            death_date=pl.coalesce(pl.col("deathtime").dt.date(), "dod"),
            death_datetime="deathtime",
            death_type_concept_id=pl.when(pl.col("deathtime").is_not_null())
            .then(c.EHR)
            .otherwise(c.REGISTRY),
        )
    )


def build(inp: Inputs) -> pl.LazyFrame:
    # only persons kept in the CDM (see person.py for the exclusion of persons without events)
    return death(inp.src.scan("hosp/patients"), inp.src.scan("hosp/admissions")).join(
        inp.cdm.scan("person").select("person_id"), on="person_id", how="semi"
    )
