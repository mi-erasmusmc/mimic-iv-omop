"""condition_occurrence: coded events in the Condition domain (see etl/events.py).

condition_end_date is left empty: MIMIC records when a diagnosis was coded, not when it ended.
"""

import polars as pl

from mimic_omop.context import Inputs
from mimic_omop.etl.sources import domain_events


def condition_occurrence(events: pl.LazyFrame) -> pl.LazyFrame:
    return events.select(
        condition_occurrence_id="event_id",
        person_id="person_id",
        condition_concept_id="concept_id",
        condition_start_date="start_date",
        condition_start_datetime="start_datetime",
        condition_type_concept_id="type_concept_id",
        condition_status_concept_id="status_concept_id",
        visit_occurrence_id="visit_occurrence_id",
        visit_detail_id="visit_detail_id",
        condition_source_value="source_value",
        condition_source_concept_id="source_concept_id",
        condition_status_source_value="status_source_value",
    )


def build(inp: Inputs) -> pl.LazyFrame:
    return condition_occurrence(domain_events(inp, "Condition"))
