from datetime import datetime

import polars as pl
from hypothesis import settings

# The property tests run Polars queries, whose time per example depends on what else the machine
# is running; a deadline would make them fail under load, not on wrong results.
settings.register_profile("default", deadline=None)
settings.load_profile("default")


def ts(s: str) -> datetime:
    return datetime.fromisoformat(s)


def admissions(*rows: dict) -> pl.LazyFrame:
    """Admissions rows with defaults for every column the builders read."""
    defaults = {
        "subject_id": 1,
        "hadm_id": 100,
        "admittime": ts("2150-01-01 10:00"),
        "dischtime": ts("2150-01-05 12:00"),
        "deathtime": None,
        "admission_type": "ELECTIVE",
        "admission_location": None,
        "discharge_location": None,
        "race": None,
        "edregtime": None,
        "insurance": None,
    }
    schema = {
        "subject_id": pl.Int64,
        "hadm_id": pl.Int64,
        "admittime": pl.Datetime("us"),
        "dischtime": pl.Datetime("us"),
        "deathtime": pl.Datetime("us"),
        "admission_type": pl.String,
        "admission_location": pl.String,
        "discharge_location": pl.String,
        "race": pl.String,
        "edregtime": pl.Datetime("us"),
        "insurance": pl.String,
    }
    return pl.LazyFrame([defaults | r for r in rows], schema=schema)


def patients(*rows: dict) -> pl.LazyFrame:
    defaults = {"subject_id": 1, "gender": "F", "anchor_age": 60, "anchor_year": 2150, "dod": None}
    schema = {
        "subject_id": pl.Int64,
        "gender": pl.String,
        "anchor_age": pl.Int64,
        "anchor_year": pl.Int64,
        "dod": pl.Date,
    }
    return pl.LazyFrame([defaults | r for r in rows], schema=schema)


def transfers(*rows: dict) -> pl.LazyFrame:
    defaults = {
        "subject_id": 1,
        "hadm_id": 100,
        "transfer_id": 30_000_001,
        "eventtype": "admit",
        "careunit": "Medicine",
        "intime": ts("2150-01-01 10:00"),
        "outtime": ts("2150-01-05 12:00"),
    }
    schema = {
        "subject_id": pl.Int64,
        "hadm_id": pl.Int64,
        "transfer_id": pl.Int64,
        "eventtype": pl.String,
        "careunit": pl.String,
        "intime": pl.Datetime("us"),
        "outtime": pl.Datetime("us"),
    }
    return pl.LazyFrame([defaults | r for r in rows], schema=schema)


def no_transfers() -> pl.LazyFrame:
    return transfers().clear()


def icustays(*rows: tuple[int, int, str, str]) -> pl.LazyFrame:
    """ICU stays: (stay_id, hadm_id, intime, outtime)."""
    return pl.LazyFrame(
        [{"stay_id": s, "hadm_id": h, "intime": ts(i), "outtime": ts(o)} for s, h, i, o in rows],
        schema={"stay_id": pl.Int64, "hadm_id": pl.Int64, "intime": pl.Datetime("us"), "outtime": pl.Datetime("us")},
    )  # fmt: skip


def cvb(*rows: tuple[str, str, str, int, str]) -> pl.LazyFrame:
    """CVB mapping rows: (source_code_set, source_code, relationship_id, target_id, domain)."""
    schema = {
        "source_code_set": pl.String,
        "source_code": pl.String,
        "source_concept_id": pl.String,
        "relationship_id": pl.String,
        "target_concept_id": pl.String,
        "target_domain_id": pl.String,
    }
    return pl.LazyFrame(
        [
            {
                "source_code_set": s,
                "source_code": code,
                "source_concept_id": "2060000000",
                "relationship_id": rel,
                "target_concept_id": str(target),
                "target_domain_id": domain,
            }
            for s, code, rel, target, domain in rows
        ],
        schema=schema,
    )
