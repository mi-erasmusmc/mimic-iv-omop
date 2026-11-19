"""fact_relationship: links between records that no CDM field holds, so far between each
microbiology specimen and its test results (see etl/microbiology.py). Susceptibilities point at
their isolate's test result through measurement_event_id instead."""

import polars as pl

from mimic_omop import concepts as c
from mimic_omop.context import Inputs
from mimic_omop.etl import microbiology
from mimic_omop.etl.events import DOMAIN_TABLE, PREFIX


def micro_records(inp: Inputs) -> pl.LazyFrame:
    """(id, domain_concept_id) of the test results from microbiologyevents, in any table."""
    parts = []
    for domain, table in DOMAIN_TABLE.items():
        # episode holds no microbiology, and is built after this table: an episode table in the
        # output directory is a previous run's
        if table in ("specimen", "episode") or not inp.cdm.has(table):
            continue
        lf = inp.cdm.scan(table)
        id_field = f"{table}_id"
        link = {"measurement": "measurement_event_id", "observation": "observation_event_id"}
        test_result = pl.col(link[table]).is_null() if table in link else pl.lit(True)
        parts.append(
            lf.filter(
                pl.col(id_field) // 10**15 == PREFIX["microbiologyevents"], test_result
            ).select(id=id_field, domain_concept_id=pl.lit(c.DOMAIN_CONCEPT[domain]))
        )
    return pl.concat(parts)


def build(inp: Inputs) -> pl.LazyFrame:
    return microbiology.specimen_links(inp.src.scan("hosp/microbiologyevents"), micro_records(inp))
