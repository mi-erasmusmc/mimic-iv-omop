"""device_exposure: coded events in the Device domain (see etl/sources.py), e.g. blood products
and enteral nutrition from icu.inputevents, and NDC-coded devices from orders and eMAR.
"""

import polars as pl

from mimic_omop.context import Inputs
from mimic_omop.etl.sources import domain_events


def device_exposure(events: pl.LazyFrame) -> pl.LazyFrame:
    return events.select(
        device_exposure_id="event_id",
        person_id="person_id",
        device_concept_id="concept_id",
        device_exposure_start_date="start_date",
        device_exposure_start_datetime="start_datetime",
        device_exposure_end_date="end_date",
        device_exposure_end_datetime="end_datetime",
        device_type_concept_id="type_concept_id",
        visit_occurrence_id="visit_occurrence_id",
        visit_detail_id="visit_detail_id",
        device_source_value="source_value",
        device_source_concept_id="source_concept_id",
    )


def build(inp: Inputs) -> pl.LazyFrame:
    return device_exposure(domain_events(inp, "Device"))
