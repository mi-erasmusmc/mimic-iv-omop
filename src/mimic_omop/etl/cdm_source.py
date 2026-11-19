"""cdm_source (one row describing this CDM) and metadata (versions and ETL choices).

THEMIS asks that reasons for excluding persons be recorded in METADATA; the count of persons
without clinical events is written there, with the vocabulary and CVB versions, the ETL version,
and the observation period strategy.
"""

import subprocess
from datetime import date
from pathlib import Path

import polars as pl

from mimic_omop import __version__
from mimic_omop.cdm.spec import CDM_VERSION
from mimic_omop.context import Inputs
from mimic_omop.etl import observation_period

CDM_VERSION_CONCEPT_ID = 902984  # OMOP CDM Version 5.5.0
SOURCE_DOCUMENTATION = "https://mimic.mit.edu/docs/iv/"


def etl_reference() -> str:
    """mimic-omop's version and, when run from a git checkout, its commit."""
    try:
        out = subprocess.run(
            ["git", "describe", "--always", "--dirty"],
            cwd=Path(__file__).parent,
            capture_output=True,
            text=True,
            timeout=5,
        )
        commit = out.stdout.strip() if out.returncode == 0 else ""
    except (OSError, subprocess.TimeoutExpired):
        commit = ""
    return f"mimic-omop {__version__}" + (f" (git {commit})" if commit else "")


def vocabulary_version(inp: Inputs) -> str:
    return (
        inp.vocab.athena("vocabulary")
        .filter(pl.col("vocabulary_id") == "None")
        .select("vocabulary_version")
        .collect()
        .item()
    )


def build_cdm_source(inp: Inputs) -> pl.LazyFrame:
    r = inp.release
    today = r.cdm_release_date or date.today()
    reference = etl_reference()
    return pl.LazyFrame(
        {
            "cdm_source_name": [f"MIMIC-IV {r.source_version}".strip()],
            "cdm_source_abbreviation": ["MIMIC-IV"],
            "cdm_holder": [r.cdm_holder],
            "source_description": [
                "MIMIC-IV: deidentified electronic health records of patients admitted to the "
                "emergency department or an intensive care unit of the Beth Israel Deaconess "
                "Medical Center, Boston. Dates are shifted per patient into 2100-2200."
            ],
            "source_documentation_reference": [SOURCE_DOCUMENTATION],
            "cdm_etl_reference": [reference],
            "source_release_date": [r.source_release_date or today],
            "cdm_release_date": [today],
            "cdm_version": [CDM_VERSION],
            "cdm_version_concept_id": [CDM_VERSION_CONCEPT_ID],
            "vocabulary_version": [vocabulary_version(inp)],
            "cdm_release_identifier": [f"{today.isoformat()} {reference}"],
        }
    )


def build_metadata(inp: Inputs) -> pl.LazyFrame:
    cvb = inp.vocab.cvb_version()
    patients = inp.src.scan("hosp/patients").select(pl.len()).collect().item()
    persons = inp.cdm.scan("person").select(pl.len()).collect().item()
    rows = [
        ("vocabulary_version", vocabulary_version(inp), None),
        ("cvb_version", f"{cvb['repo']}@{cvb['ref']}", None),
        ("etl_version", etl_reference(), None),
        ("source_version", f"MIMIC-IV {inp.release.source_version}".strip(), None),
        (
            "observation_period_strategy",
            observation_period.STRATEGIES[inp.release.observation_periods].description,
            None,
        ),
        (
            "persons_excluded",
            "persons without any clinical event, hence without an observation period",
            float(patients - persons),
        ),
    ]
    return pl.LazyFrame(
        {
            "metadata_id": list(range(1, len(rows) + 1)),
            "metadata_concept_id": [0] * len(rows),
            "metadata_type_concept_id": [0] * len(rows),
            "name": [r[0] for r in rows],
            "value_as_string": [r[1] for r in rows],
            "value_as_number": [r[2] for r in rows],
            "metadata_date": [inp.release.cdm_release_date or date.today()] * len(rows),
        },
        schema_overrides={"value_as_number": pl.Float64},
    )
