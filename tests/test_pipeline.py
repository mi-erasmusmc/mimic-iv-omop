import shutil
from pathlib import Path

import polars as pl
import pytest

from mimic_omop import pipeline
from mimic_omop.cdm.schema import conform
from mimic_omop.context import Release

DATA = Path(__file__).parents[1] / "data"
DEMO = DATA / "mimic-iv-demo-2.2"
VOCAB = DATA / "vocab"


def write_table(out: Path, table: str, df: pl.DataFrame):
    (out / table).mkdir(parents=True)
    df.write_parquet(out / table / "part-0000.parquet")


def test_foreign_keys_between_cdm_tables(tmp_path):
    write_table(tmp_path, "person", pl.DataFrame({"person_id": [1, 2]}))
    write_table(tmp_path, "death", pl.DataFrame({"person_id": [2, 3]}))
    assert pipeline.check_foreign_keys(tmp_path) == [
        "death.person_id: 1 values not in person.person_id"
    ]


def test_failed_validation_leaves_previous_output_in_place(tmp_path, monkeypatch):
    write_table(tmp_path / "cdm", "death", pl.DataFrame({"person_id": [1]}))
    bad = pipeline.Table("death", lambda inp: pl.LazyFrame({"person_id": [1, 1]}))
    monkeypatch.setattr(pipeline, "REGISTRY", (bad,))
    monkeypatch.setattr(pipeline, "BUILDERS", {"death": bad})
    with pytest.raises(pipeline.CdmValidationError, match="death_date"):
        pipeline.run(tmp_path / "src", tmp_path / "vocab", tmp_path / "cdm")
    assert pl.read_parquet(tmp_path / "cdm" / "death")["person_id"].to_list() == [1]


HAS_ATHENA = (VOCAB / "athena" / "concept.parquet").exists()
BASE_COUNTS = {
    "person": 100,
    "death": 31,
    "care_site": 31,
    # 275 admissions, 54 ED-only stays, 835 Outpatient Visit days (omr, date-only microbiology)
    # and 197 Laboratory Visit days (labs and microbiology without an admission)
    "visit_occurrence": 1361,
    "visit_detail": 915,
    "observation_period": 113,
}
SOURCES = [
    "hosp/patients",
    "hosp/admissions",
    "hosp/transfers",
    "hosp/diagnoses_icd",
    "hosp/procedures_icd",
    "hosp/hcpcsevents",
    "hosp/drgcodes",
    "hosp/labevents",
    "hosp/prescriptions",
    "hosp/pharmacy",
    "hosp/emar",
    "hosp/emar_detail",
    "hosp/omr",
    "hosp/microbiologyevents",
    "icu/inputevents",
    "icu/icustays",
    "icu/chartevents",
    "icu/outputevents",
    "icu/procedureevents",
    "icu/datetimeevents",
]
needs_athena = pytest.mark.skipif(not HAS_ATHENA, reason="needs an Athena vocabulary (load-athena)")


def test_concept_fields_are_checked_against_the_vocabulary(tmp_path):
    concept = pl.LazyFrame(
        {
            "concept_id": [8507, 44814653],
            "standard_concept": ["S", None],
            "invalid_reason": [None, None],
        },
        schema_overrides={"invalid_reason": pl.String},
    )
    person = pl.LazyFrame(
        {
            "person_id": [1, 2, 3, 4],
            # standard, non-standard, unknown, unmapped
            "gender_concept_id": [8507, 44814653, 123, 0],
            # a custom concept is counted, not failed
            "gender_source_concept_id": [44814653, 0, 2_061_000_000, 0],
        }
    )
    write_table(tmp_path, "person", conform(person, "person").collect())
    problems, custom = pipeline.check_concepts(tmp_path, concept)
    assert problems == [
        "person.gender_concept_id: 1 concept ids not in the vocabulary",
        "person.gender_concept_id: 1 non-standard or invalid concepts",
    ]
    assert custom == {"person.gender_source_concept_id": 1}


@pytest.fixture(scope="module")
def demo_cdm(tmp_path_factory) -> Path:
    """The demo built once, and again with 4 buckets. Without Athena (as in CI, since Athena
    vocabularies cannot be redistributed) only the tables that need no Athena vocabulary."""
    if not DEMO.exists() or not (VOCAB / "cvb_mimic_mapping.parquet").exists():
        pytest.skip("run `mimic-omop fetch-demo` and `mimic-omop fetch-cvb` first")
    from mimic_omop.source import convert

    tmp = tmp_path_factory.mktemp("demo")
    convert(DEMO, tmp / "src", SOURCES)
    # the large vocabulary tables are plain copies of Athena; leave them out to keep the test fast
    large = {"concept_relationship", "concept_ancestor", "concept_synonym", "drug_strength",
             "concept_metadata", "concept_relationship_metadata", "pack_content"}  # fmt: skip
    tables = [t for t in pipeline.BUILDERS if t not in large] if HAS_ATHENA else list(BASE_COUNTS)
    counts = pipeline.run(tmp / "src", VOCAB, tmp / "cdm", tables)
    # event table counts depend on the vocabulary version, so only these are pinned
    assert {t: counts[t] for t in BASE_COUNTS} == BASE_COUNTS
    pipeline.run(tmp / "src", VOCAB, tmp / "cdm4", tables, buckets=4)
    return tmp


def scan(root: Path, table: str) -> pl.LazyFrame:
    return pl.scan_parquet(root / table / "part-*.parquet")


@pytest.mark.demo
def test_demo_output_does_not_depend_on_buckets(demo_cdm):
    for table in [p.name for p in (demo_cdm / "cdm").iterdir() if p.is_dir()]:
        if table in pipeline.VOCABULARY_TABLES:
            continue
        a = scan(demo_cdm / "cdm", table).sort(pl.all()).collect()
        b = scan(demo_cdm / "cdm4", table).sort(pl.all()).collect()
        assert a.equals(b), table


@pytest.mark.demo
def test_demo_details_inside_visits_inside_periods(demo_cdm):
    cdm = demo_cdm / "cdm"
    visits = scan(cdm, "visit_occurrence")
    outside = (
        scan(cdm, "visit_detail")
        .join(visits, on="visit_occurrence_id")
        .filter(
            (pl.col("visit_detail_start_datetime") < pl.col("visit_start_datetime"))
            | (pl.col("visit_detail_end_datetime") > pl.col("visit_end_datetime"))
        )
    )
    assert outside.collect().is_empty()

    covered = (
        visits.join(scan(cdm, "observation_period"), on="person_id")
        .filter(
            pl.col("visit_start_date") >= pl.col("observation_period_start_date"),
            pl.col("visit_end_date") <= pl.col("observation_period_end_date"),
        )
        .select(pl.col("visit_occurrence_id").n_unique())
        .collect()
        .item()
    )
    assert covered == visits.select(pl.len()).collect().item()


@pytest.mark.demo
@needs_athena
def test_demo_every_coded_source_row_is_accounted_for(demo_cdm):
    """Each kept row of a coded source table yields records in some CDM table, or in a domain
    whose table is not built yet, under its own source code; event ids identify their source row
    (id // 10)."""
    from mimic_omop import accounting
    from mimic_omop.etl import events

    assert set(accounting.COUNTED) == set(events.PREFIX)
    cdm = demo_cdm / "cdm"
    per_code = accounting.codes(demo_cdm / "src", cdm)
    assert per_code.filter(pl.col("missing") != 0).is_empty()
    summary = accounting.summary(per_code)
    # every converted source (the demo has no MIMIC-IV-ED module)
    converted = {
        n
        for n, c in accounting.COUNTED.items()
        if (demo_cdm / "src" / f"{c.table}.parquet").exists()
    }
    assert (
        set(summary["source"]) == converted == {s for s in events.PREFIX if not s.startswith("ed_")}
    )
    # rows left out in the demo: eMAR actions other than administrations, and ICU dates charted
    # again
    dropped = dict(summary.select("source", "dropped_rows").iter_rows())
    assert {s for s, n in dropped.items() if n} == {"emar", "datetimeevents"}

    # and nothing is written twice
    for table, (column, _, _) in accounting.EVENT_TABLES.items():
        assert scan(cdm, table).select(pl.col(column).is_unique().all()).collect().item()


@pytest.mark.demo
def test_demo_follows_cdm_visit_and_period_conventions(demo_cdm):
    cdm = demo_cdm / "cdm"
    periods = scan(cdm, "observation_period")
    # every person has an observation period
    persons = scan(cdm, "person").join(periods, on="person_id", how="anti")
    assert persons.collect().is_empty()
    # periods of a person neither overlap nor are adjacent
    pairs = periods.join(periods, on="person_id").filter(
        pl.col("observation_period_id") < pl.col("observation_period_id_right"),
        pl.col("observation_period_start_date") <= pl.col("observation_period_end_date_right") + pl.duration(days=1),
        pl.col("observation_period_start_date_right") <= pl.col("observation_period_end_date") + pl.duration(days=1),
    )  # fmt: skip
    assert pairs.collect().is_empty()
    # multi-day visits share no days other than their first and last
    visits = scan(cdm, "visit_occurrence")
    overlap = visits.join(visits, on="person_id").filter(
        pl.col("visit_occurrence_id") < pl.col("visit_occurrence_id_right"),
        pl.col("visit_start_date") < pl.col("visit_end_date_right"),
        pl.col("visit_start_date_right") < pl.col("visit_end_date"),
    )
    assert overlap.collect().is_empty()


@pytest.mark.demo
def test_demo_encounter_based_observation_periods(demo_cdm, tmp_path):
    shutil.copytree(demo_cdm / "cdm", tmp_path / "cdm")
    release = Release(observation_periods="encounter")
    tables = ["observation_period", "metadata"] if HAS_ATHENA else ["observation_period"]
    counts = pipeline.run(demo_cdm / "src", VOCAB, tmp_path / "cdm", tables, release=release)
    # review R1. Without Athena only the BASE_COUNTS tables exist, so the periods come from visits
    # alone; the event tables add 19 for events outside every visit, 8 of them for ICU dates (line
    # insertions, R34)
    assert counts["observation_period"] == (1235 if HAS_ATHENA else 1216)
    # every visit still lies in a period
    periods = scan(tmp_path / "cdm", "observation_period")
    outside = (
        scan(tmp_path / "cdm", "visit_occurrence")
        .join(periods, on="person_id", how="left")
        .group_by("visit_occurrence_id")
        .agg(
            inside=(
                (pl.col("visit_start_date") >= pl.col("observation_period_start_date"))
                & (pl.col("visit_end_date") <= pl.col("observation_period_end_date"))
            ).any()
        )
        .filter(~pl.col("inside"))
    )
    assert outside.collect().is_empty()
    if HAS_ATHENA:
        meta = dict(
            scan(tmp_path / "cdm", "metadata")
            .select("name", "value_as_string")
            .collect()
            .iter_rows()
        )
        assert meta["observation_period_strategy"].startswith("encounter-based")
    with pytest.raises(ValueError, match="observation period strategy"):
        pipeline.run(
            demo_cdm / "src",
            VOCAB,
            tmp_path / "cdm",
            tables,
            release=Release(observation_periods="x"),
        )


@pytest.mark.demo
@needs_athena
def test_demo_eras_and_cdm_source(demo_cdm):
    cdm = demo_cdm / "cdm"
    mapped = scan(cdm, "condition_occurrence").filter(pl.col("condition_concept_id") != 0)
    counted = scan(cdm, "condition_era").select(pl.col("condition_occurrence_count").sum())
    assert counted.collect().item() == mapped.select(pl.len()).collect().item()

    ingredient = (
        scan(cdm, "drug_era")
        .join(scan(cdm, "concept"), left_on="drug_concept_id", right_on="concept_id")
        .select((pl.col("concept_class_id") == "Ingredient").all(), (pl.col("gap_days") >= 0).all())
    )
    assert ingredient.collect().row(0) == (True, True)

    source = scan(cdm, "cdm_source").collect()
    assert source.height == 1
    assert (source["cdm_version"][0], source["cdm_version_concept_id"][0]) == ("5.5", 902984)
    meta = dict(scan(cdm, "metadata").select("name", "value_as_number").collect().iter_rows())
    persons = scan(cdm, "person").select(pl.len()).collect().item()
    assert meta["persons_excluded"] == 100 - persons


@pytest.mark.demo
def test_demo_rebuilding_a_table_in_an_existing_output_gives_the_same_table(demo_cdm, tmp_path):
    """A builder must not read tables of a previous run that the run has not rebuilt yet."""
    if not HAS_ATHENA:
        pytest.skip("needs Athena")
    shutil.copytree(demo_cdm / "cdm", tmp_path / "cdm")
    pipeline.run(demo_cdm / "src", VOCAB, tmp_path / "cdm", ["fact_relationship"])
    before = scan(demo_cdm / "cdm", "fact_relationship").collect().sort(pl.all())
    assert scan(tmp_path / "cdm", "fact_relationship").collect().sort(pl.all()).equals(before)
