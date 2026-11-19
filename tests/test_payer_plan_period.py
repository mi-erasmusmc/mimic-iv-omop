from datetime import date
from pathlib import Path

import polars as pl
import pytest
from conftest import admissions, ts

from mimic_omop.etl import payer_plan_period as ppp

SOPT = pl.LazyFrame(
    [(280, "1", "S"), (289, "2", "S"), (327, "5", "S"), (437, "82", "S"), (452, "98", None)],
    schema=["concept_id", "concept_code", "standard_concept"],
    orient="row",
).with_columns(vocabulary_id=pl.lit("SOPT"), invalid_reason=pl.lit(None, pl.String))


def test_each_admission_with_an_insurance_is_a_period_with_its_sopt_payer():
    adm = admissions(
        {"hadm_id": 20_000_001, "insurance": "Medicare"},
        {"hadm_id": 20_000_002, "insurance": "Other", "admittime": ts("2150-03-01 10:00"),
         "dischtime": ts("2150-03-04 09:00")},
        {"hadm_id": 20_000_003, "insurance": None, "admittime": ts("2150-05-01 10:00"),
         "dischtime": ts("2150-05-02 09:00")},
    )  # fmt: skip
    df = ppp.payer_plan_period(adm, ppp.payer_map(ppp.local_mapping(), SOPT)).collect()
    assert df.sort("payer_plan_period_id").select(
        "payer_plan_period_id", "payer_plan_period_start_date", "payer_plan_period_end_date",
        "payer_concept_id", "payer_source_value",
    ).rows() == [
        (20_000_001, date(2150, 1, 1), date(2150, 1, 5), 280, "Medicare"),
        (20_000_002, date(2150, 3, 1), date(2150, 3, 4), 0, "Other"),  # no SOPT equivalent
    ]  # fmt: skip


VOCAB = Path(__file__).parents[1] / "data" / "vocab"


@pytest.mark.skipif(not (VOCAB / "athena" / "concept.parquet").exists(), reason="needs Athena")
def test_every_code_of_the_local_payer_mapping_resolves():
    concept = pl.scan_parquet(VOCAB / "athena" / "concept.parquet")
    resolved = ppp.payer_map(ppp.local_mapping(), concept).collect()
    assert resolved.height == ppp.local_mapping().collect().height
