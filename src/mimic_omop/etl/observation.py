"""observation: coded events in the Observation domain (see etl/events.py).

For 'history of' style codes the vocabulary maps the code to an Observation concept plus a
'Maps to value' concept, which becomes value_as_concept_id.
"""

import polars as pl

from mimic_omop.context import Inputs
from mimic_omop.etl.sources import domain_events


def observation(events: pl.LazyFrame) -> pl.LazyFrame:
    return events.select(
        observation_id="event_id",
        person_id="person_id",
        observation_concept_id="concept_id",
        observation_date="start_date",
        observation_datetime="start_datetime",
        observation_type_concept_id="type_concept_id",
        value_as_number="value_as_number",
        value_as_string="value_as_string",
        value_as_concept_id="value_as_concept_id",
        unit_concept_id="unit_concept_id",
        visit_occurrence_id="visit_occurrence_id",
        visit_detail_id="visit_detail_id",
        observation_source_value="source_value",
        observation_source_concept_id="source_concept_id",
        unit_source_value="unit_source_value",
        value_source_value="value_source_value",
        observation_event_id="event_link_id",
        obs_event_field_concept_id="event_link_field_concept_id",
    )


def build(inp: Inputs) -> pl.LazyFrame:
    return observation(domain_events(inp, "Observation"))
