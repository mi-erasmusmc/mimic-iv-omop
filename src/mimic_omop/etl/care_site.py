"""care_site: one per hospital care unit (hosp.transfers.careunit).

care_site_id is derived from the unit name with SHA-256, so ids are identical between the demo
and the full data and between runs. Polars' own hash is not stable across versions, which is why
the ids are computed in Python; there are only a few dozen units.
"""

import hashlib

import polars as pl

from mimic_omop.context import Inputs


def stable_id(name: str) -> int:
    return int.from_bytes(hashlib.sha256(name.encode()).digest()[:4], "big") & 0x7FFFFFFF


def care_site(transfers: pl.LazyFrame, place_map: pl.LazyFrame) -> pl.LazyFrame:
    units = (
        transfers.filter(pl.col("eventtype") != "discharge", pl.col("careunit").is_not_null())
        .select(pl.col("careunit").unique().sort())
        .collect()["careunit"]
    )
    return (
        pl.LazyFrame({"careunit": units, "care_site_id": [stable_id(u) for u in units]})
        .join(
            place_map.select(careunit="source_code", place="target_concept_id"),
            on="careunit",
            how="left",
        )
        .select(
            "care_site_id",
            care_site_name="careunit",
            place_of_service_concept_id=pl.col("place").fill_null(0),
            care_site_source_value="careunit",
            place_of_service_source_value="careunit",
        )
    )


def build(inp: Inputs) -> pl.LazyFrame:
    return care_site(
        inp.src.scan("hosp/transfers"),
        inp.vocab.lookup("mimiciv_hosp.transfers.careunit", domains=("Visit",)),
    )
