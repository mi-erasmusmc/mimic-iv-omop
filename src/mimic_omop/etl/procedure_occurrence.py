"""procedure_occurrence: coded events in the Procedure domain (see etl/events.py)."""

import polars as pl

from mimic_omop.context import Inputs
from mimic_omop.etl.sources import domain_events


def procedure_occurrence(events: pl.LazyFrame) -> pl.LazyFrame:
    return events.select(
        procedure_occurrence_id="event_id",
        person_id="person_id",
        procedure_concept_id="concept_id",
        procedure_date="start_date",
        procedure_datetime="start_datetime",
        procedure_end_date="end_date",
        procedure_end_datetime="end_datetime",
        procedure_type_concept_id="type_concept_id",
        visit_occurrence_id="visit_occurrence_id",
        visit_detail_id="visit_detail_id",
        procedure_source_value="source_value",
        procedure_source_concept_id="source_concept_id",
    )


def build(inp: Inputs) -> pl.LazyFrame:
    return procedure_occurrence(domain_events(inp, "Procedure"))
