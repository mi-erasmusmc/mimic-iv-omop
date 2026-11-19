"""drug_exposure: coded events in the Drug domain (see etl/drug_events.py): orders from
hosp.prescriptions, administrations from hosp.emar and icu.inputevents, and codes of other
sources that map to Drug concepts.
"""

import polars as pl

from mimic_omop.context import Inputs
from mimic_omop.etl.sources import domain_events


def drug_exposure(events: pl.LazyFrame) -> pl.LazyFrame:
    return events.select(
        drug_exposure_id="event_id",
        person_id="person_id",
        drug_concept_id="concept_id",
        drug_exposure_start_date="start_date",
        drug_exposure_start_datetime="start_datetime",
        # records without an end (codes of other sources routed here) end on their start date
        drug_exposure_end_date=pl.coalesce("end_date", "start_date"),
        drug_exposure_end_datetime="end_datetime",
        verbatim_end_date="verbatim_end_date",
        drug_type_concept_id="type_concept_id",
        quantity="quantity",
        route_concept_id="route_concept_id",
        visit_occurrence_id="visit_occurrence_id",
        visit_detail_id="visit_detail_id",
        drug_source_value="source_value",
        drug_source_concept_id="source_concept_id",
        route_source_value="route_source_value",
        dose_unit_source_value="dose_unit_source_value",
    )


def build(inp: Inputs) -> pl.LazyFrame:
    return drug_exposure(domain_events(inp, "Drug"))
