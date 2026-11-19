"""visit_detail: one per stay in a care unit (hosp.transfers rows other than 'discharge').

Conventions:
- visit_detail_id = transfer_id; care_site is the care unit.
- visit_detail_concept_id by care unit: ED units are Emergency Room, units named as intensive
  care (MICU, SICU, CCU, ...) are Intensive Care, 'Observation' is Observation Room, others are
  Inpatient.
- Stays without an hadm_id belong to the ED-only visits built in visit_occurrence.
- preceding_visit_detail_id is the person's previous stay by intime, also across visits, as the
  CDM defines it (and as preceding_visit_occurrence_id is set).
- A stay without an outtime (103 in MIMIC-IV 3.1) ends when the next transfer of its admission
  starts, the patient having stayed until moved; without a next transfer, at its own intime.
"""

import polars as pl

from mimic_omop import concepts as c
from mimic_omop.context import Inputs
from mimic_omop.etl.visit_occurrence import unadmitted_stays

ICU_PATTERN = r"Intensive Care|ICU|\(CCU\)"


def unit_concept(careunit: pl.Expr) -> pl.Expr:
    return (
        pl.when(careunit.str.starts_with("Emergency Department"))
        .then(c.EMERGENCY_ROOM)
        .when(careunit.str.contains(ICU_PATTERN))
        .then(c.INTENSIVE_CARE)
        .when(careunit == "Observation")
        .then(c.OBSERVATION_ROOM)
        .otherwise(c.INPATIENT)
    )


def visit_detail(transfers: pl.LazyFrame, care_site: pl.LazyFrame) -> pl.LazyFrame:
    next_intime = (
        pl.col("intime").shift(-1).over("subject_id", "hadm_id", order_by=["intime", "transfer_id"])
    )
    return (
        transfers.with_columns(next_intime=next_intime)
        .filter(pl.col("eventtype") != "discharge")
        .join(unadmitted_stays(transfers), on="transfer_id", how="left")
        .join(
            care_site.select("care_site_id", careunit="care_site_source_value"),
            on="careunit",
            how="left",
        )
        .with_columns(
            visit=pl.coalesce("hadm_id", "visit_occurrence_id"),
            end=pl.max_horizontal(pl.coalesce("outtime", "next_intime"), "intime"),
        )
        .select(
            visit_detail_id="transfer_id",
            person_id="subject_id",
            visit_detail_concept_id=unit_concept(pl.col("careunit")),
            visit_detail_start_date=pl.col("intime").dt.date(),
            visit_detail_start_datetime="intime",
            visit_detail_end_date=pl.col("end").dt.date(),
            visit_detail_end_datetime="end",
            visit_detail_type_concept_id=pl.lit(c.EHR),
            care_site_id="care_site_id",
            visit_detail_source_value="careunit",
            visit_detail_source_concept_id=pl.lit(0),
            # CDM: the detail "that occurred for the person prior to the given visit detail
            # record", so the first stay of an admission links to the person's previous stay
            preceding_visit_detail_id=pl.col("transfer_id")
            .shift(1)
            .over("subject_id", order_by=["intime", "transfer_id"]),
            visit_occurrence_id="visit",
        )
    )


def build(inp: Inputs) -> pl.LazyFrame:
    return visit_detail(inp.src.scan("hosp/transfers"), inp.cdm.scan("care_site"))
