"""specimen: microbiology specimens (see etl/microbiology.py), and coded events of any source that
map to a Specimen concept (none in MIMIC-IV 3.1)."""

import polars as pl

from mimic_omop.context import Inputs
from mimic_omop.etl import microbiology
from mimic_omop.etl.sources import MICRO, domain_events


def from_events(events: pl.LazyFrame) -> pl.LazyFrame:
    return events.select(
        specimen_id="event_id",
        person_id="person_id",
        specimen_concept_id="concept_id",
        specimen_type_concept_id="type_concept_id",
        specimen_date="start_date",
        specimen_datetime="start_datetime",
        specimen_source_value="source_value",
        visit_occurrence_id="visit_occurrence_id",
        visit_detail_id="visit_detail_id",
    )


def build(inp: Inputs) -> pl.LazyFrame:
    micro = microbiology.specimens(
        inp.src.scan("hosp/microbiologyevents"),
        inp.cdm.scan("visit_occurrence"),
        inp.cdm.scan("visit_detail"),
        inp.vocab.cvb_code_map(f"{MICRO}.spec_itemid"),
    )
    return pl.concat([micro, from_events(domain_events(inp, "Specimen"))], how="diagonal_relaxed")
