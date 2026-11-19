"""measurement: coded events in the Measurement domain (see etl/events.py), currently labs from
hosp.labevents plus diagnosis and procedure codes that map to Measurement concepts.
"""

import polars as pl

from mimic_omop.context import Inputs
from mimic_omop.etl.sources import domain_events


def measurement(events: pl.LazyFrame) -> pl.LazyFrame:
    return events.select(
        measurement_id="event_id",
        person_id="person_id",
        measurement_concept_id="concept_id",
        measurement_date="start_date",
        measurement_datetime="start_datetime",
        measurement_type_concept_id="type_concept_id",
        operator_concept_id="operator_concept_id",
        value_as_number="value_as_number",
        value_as_concept_id="value_as_concept_id",
        unit_concept_id="unit_concept_id",
        range_low="range_low",
        range_high="range_high",
        visit_occurrence_id="visit_occurrence_id",
        visit_detail_id="visit_detail_id",
        measurement_source_value="source_value",
        measurement_source_concept_id="source_concept_id",
        unit_source_value="unit_source_value",
        value_source_value="value_source_value",
        measurement_event_id="event_link_id",
        meas_event_field_concept_id="event_link_field_concept_id",
    )


def build(inp: Inputs) -> pl.LazyFrame:
    return measurement(domain_events(inp, "Measurement"))
