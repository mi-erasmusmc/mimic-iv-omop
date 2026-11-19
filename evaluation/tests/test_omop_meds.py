from datetime import datetime
from pathlib import Path

import polars as pl
import pytest

from mimic_eval import omop_meds

ROOT = Path(__file__).parents[2]
DEMO_CDM = ROOT / "data" / "demo-cdm"
DEMO_SRC = ROOT / "data" / "demo-parquet"
DEMO_MEDS = Path.home() / "projects/meds_mimic/output/MEDS_cohort"  # the demo in MEDS


def t(s: str) -> datetime:
    return datetime.fromisoformat(s)


def test_consecutive_icu_care_unit_stays_are_one_icu_stay():
    details = pl.LazyFrame(
        {"person_id": [1, 1, 1, 1], "visit_occurrence_id": [7, 7, 7, 7],
         "visit_detail_concept_id": [32037, 32037, 9201, 32037],
         "visit_detail_start_datetime": [t("2150-01-01 10:00"), t("2150-01-02 10:00"), t("2150-01-03 10:00"), t("2150-01-04 10:00")],
         "visit_detail_end_datetime": [t("2150-01-02 10:00"), t("2150-01-03 10:00"), t("2150-01-04 10:00"), t("2150-01-05 10:00")]}
    )  # fmt: skip
    stays = omop_meds.icu_stays(details).collect().sort("start")
    # MICU then SICU without a gap is one stay; after a ward stay, a new one
    assert stays.select("start", "end").rows() == [
        (t("2150-01-01 10:00"), t("2150-01-03 10:00")),
        (t("2150-01-04 10:00"), t("2150-01-05 10:00")),
    ]


needs_demo = pytest.mark.skipif(
    not (DEMO_CDM.exists() and DEMO_MEDS.exists() and (DEMO_CDM / "concept").exists()),
    reason="needs the demo CDM (with vocabulary) and the demo in MEDS",
)


@pytest.fixture(scope="module")
def demo_meds(tmp_path_factory) -> tuple[Path, dict[str, int]]:
    out = tmp_path_factory.mktemp("meds") / "cdm-meds"
    return out, omop_meds.convert(DEMO_CDM, DEMO_MEDS, out)


@needs_demo
def test_every_cdm_record_becomes_one_event_in_the_source_meds_layout(demo_meds):
    demo_meds, counts = demo_meds
    meds = pl.read_parquet(demo_meds / "data/*/*.parquet")
    for table in omop_meds.TABLES:
        n = pl.scan_parquet(DEMO_CDM / table / "*.parquet").select(pl.len()).collect().item()
        assert counts[table] == n, table
    assert meds.height == sum(counts.values())
    source = pl.read_parquet(DEMO_MEDS / "metadata/subject_splits.parquet")
    ours = pl.read_parquet(demo_meds / "metadata/subject_splits.parquet")
    assert ours.sort("subject_id").equals(source.select("subject_id", "split").sort("subject_id"))
    for f in (demo_meds / "data").glob("*/*.parquet"):
        df = pl.read_parquet(f)
        assert df.equals(df.sort("subject_id", "time", "code", nulls_last=False))


@needs_demo
def test_icu_stays_are_mimics_and_diagnoses_are_at_the_visit_end(demo_meds):
    meds = pl.read_parquet(demo_meds[0] / "data/*/*.parquet")
    icustays = pl.read_parquet(DEMO_SRC / "icu/icustays.parquet")
    assert meds.filter(pl.col("code") == "ICU_ADMISSION").height == icustays.height
    # billing diagnoses (ICD-CM source codes) at their visit's end; the "start" variant keeps the
    # CDM's date, the visit start
    concepts = omop_meds.concept_codes(DEMO_CDM)
    co = pl.scan_parquet(DEMO_CDM / "condition_occurrence/*.parquet")
    icd = concepts.filter(pl.col("vocabulary_id").is_in(omop_meds.DIAGNOSIS_VOCABULARIES))
    billed = co.filter(pl.col("condition_type_concept_id") == omop_meds.BILLING).join(
        icd.select(condition_source_concept_id="concept_id"), on="condition_source_concept_id"
    )
    visits = pl.scan_parquet(DEMO_CDM / "visit_occurrence/*.parquet").select(
        "visit_occurrence_id", "visit_start_datetime", "visit_end_datetime"
    )
    expected = billed.join(visits, on="visit_occurrence_id").collect()
    assert expected.height > 0
    for mode, column in (("end", "visit_end_datetime"), ("start", "visit_start_datetime")):
        events = omop_meds.clinical_events(
            DEMO_CDM, concepts, "condition_occurrence", "standard", mode
        )
        times = events.select("subject_id", "time").collect()
        want = expected.select(subject_id="person_id", time=column)
        assert want.join(times, on=["subject_id", "time"], how="anti").is_empty(), mode


@needs_demo
def test_visit_places_are_events_at_the_visit_ends(demo_meds):
    meds = pl.scan_parquet(demo_meds[0] / "data/*/*.parquet")
    visits = pl.scan_parquet(DEMO_CDM / "visit_occurrence/*.parquet")
    for column, prefix, time in (
        ("admitted_from", "ADMITTED_FROM//", "visit_start_datetime"),
        ("discharged_to", "DISCHARGED_TO//", "visit_end_datetime"),
    ):
        placed = visits.filter(
            (pl.col(f"{column}_concept_id").fill_null(0) != 0)
            | pl.col(f"{column}_source_value").is_not_null()
        )
        events = meds.filter(pl.col("code").str.starts_with(prefix))
        assert events.select(pl.len()).collect().item() == placed.select(pl.len()).collect().item()
        # a place without a concept keeps its source value, never "No matching concept"
        assert events.filter(pl.col("code").str.contains("No matching")).collect().is_empty()
        want = placed.select(subject_id="person_id", time=time).collect()
        got = events.select("subject_id", "time").collect()
        assert want.join(got, on=["subject_id", "time"], how="anti").is_empty(), column
