from datetime import date

import polars as pl
from conftest import ts

from mimic_omop import concepts as c
from mimic_omop.etl import microbiology
from mimic_omop.etl.events import PREFIX

VISITS = pl.LazyFrame(
    {
        "person_id": [1, 1],
        "visit_occurrence_id": [20_000_001, 2 * 10**12 + 1],
        "visit_start_date": [date(2150, 1, 1), date(2150, 2, 1)],
        "visit_start_datetime": [ts("2150-01-01 08:00"), ts("2150-02-01 00:00")],
        "visit_end_date": [date(2150, 1, 9), date(2150, 2, 1)],
        "visit_end_datetime": [ts("2150-01-09 12:00"), ts("2150-02-01 23:59")],
    }
)
DETAILS = pl.LazyFrame(
    schema={"visit_occurrence_id": pl.Int64, "visit_detail_id": pl.Int64,
            "visit_detail_start_datetime": pl.Datetime("us"), "visit_detail_end_datetime": pl.Datetime("us")}
)  # fmt: skip
MAP_SCHEMA = {
    "source_code": pl.String, "source_concept_id": pl.Int64, "target_concept_id": pl.Int64,
    "target_domain_id": pl.String,
}  # fmt: skip


def code_map(*rows: tuple) -> pl.LazyFrame:
    return pl.LazyFrame(list(rows), schema=MAP_SCHEMA, orient="row")


TESTS = code_map(
    ("90201", 2_062_000_001, 3023368, "Measurement"),  # blood culture
    ("microbiologyevents.test_itemid||90039", 2_062_000_002, 3040209, "Measurement"),  # urine culture, prefixed by CVB
)  # fmt: skip
ORGANISMS = code_map(("80002", 2_062_000_010, 4011683, "Observation"))  # E. coli; 80053 unmapped
ANTIBIOTICS = code_map(
    ("90015", 2_062_000_020, 3007481, "Measurement"),  # ampicillin susceptibility
    ("90004", 2_062_000_021, 3010548, "Measurement"),  # ciprofloxacin susceptibility
)
INTERPRETATION = code_map(
    ("S", 0, 45884178, "Meas Value"),
    ("microbiologyevents.interpretation||I", 0, 45881795, "Meas Value"),
    ("R", 0, 45878594, "Meas Value"),
)
SPECIMENS = code_map(("70012", 2_062_000_030, 4001225, "Specimen"), ("70079", 2_062_000_031, 4046280, "Specimen"))  # fmt: skip


def micro_rows(*rows: dict) -> pl.LazyFrame:
    defaults = {
        "subject_id": 1, "hadm_id": 20_000_001, "chartdate": date(2150, 1, 2),
        "charttime": ts("2150-01-02 10:00"), "test_seq": 1, "org_itemid": None, "org_name": None,
        "isolate_num": None, "ab_itemid": None, "dilution_comparison": None, "dilution_value": None,
        "interpretation": None, "comments": None,
    }  # fmt: skip
    schema = {
        "microevent_id": pl.Int64, "subject_id": pl.Int64, "hadm_id": pl.Int64,
        "micro_specimen_id": pl.Int64, "chartdate": pl.Date, "charttime": pl.Datetime("us"),
        "spec_itemid": pl.Int64, "spec_type_desc": pl.String, "test_seq": pl.Int64,
        "test_itemid": pl.Int64, "org_itemid": pl.Int64, "org_name": pl.String,
        "isolate_num": pl.Int64, "ab_itemid": pl.Int64, "dilution_comparison": pl.String,
        "dilution_value": pl.Float64, "interpretation": pl.String, "comments": pl.String,
    }  # fmt: skip
    return pl.LazyFrame([defaults | r for r in rows], schema=schema)


BLOOD = {
    "micro_specimen_id": 501,
    "spec_itemid": 70012,
    "spec_type_desc": "BLOOD CULTURE",
    "test_itemid": 90201,
}
URINE = {
    "micro_specimen_id": 502,
    "spec_itemid": 70079,
    "spec_type_desc": "URINE",
    "test_itemid": 90039,
}
ECOLI = {"org_itemid": 80002, "org_name": "ESCHERICHIA COLI", "isolate_num": 1}
MICRO = micro_rows(
    BLOOD | {"microevent_id": 1, "comments": "NO GROWTH."},
    URINE | ECOLI | {"microevent_id": 2, "ab_itemid": 90015, "interpretation": "R",
                     "dilution_comparison": "=>", "dilution_value": 32.0},
    URINE | ECOLI | {"microevent_id": 3, "ab_itemid": 90004, "interpretation": "S",
                     "dilution_comparison": "<=", "dilution_value": 0.25},
    URINE | {"microevent_id": 4, "org_itemid": 80053, "org_name": "ENTEROCOCCUS SP.", "isolate_num": 2},
    # an outpatient specimen with a date only
    BLOOD | {"microevent_id": 5, "micro_specimen_id": 503, "hadm_id": None, "charttime": None,
             "chartdate": date(2150, 2, 1), "comments": "NO GROWTH."},
)  # fmt: skip


def events() -> pl.DataFrame:
    return (
        microbiology.micro_events(MICRO, VISITS, DETAILS, TESTS, ORGANISMS, ANTIBIOTICS, INTERPRETATION)
        .collect()
        .sort("event_id")
    )  # fmt: skip


def eid(microevent_id: int, k: int = 0) -> int:
    return PREFIX["microbiologyevents"] * 10**15 + microevent_id * 10 + k


def test_test_results_carry_the_organism_or_the_comment():
    df = events().filter(pl.col("event_link_id").is_null())
    assert df.select("event_id", "concept_id", "value_as_concept_id", "value_source_value", "visit_occurrence_id").rows() == [
        (eid(1), 3023368, None, "NO GROWTH.", 20_000_001),
        # E. coli is known only through its susceptibilities: a derived record (k = 5)
        (eid(2, microbiology.ISOLATE_K), 3040209, 4011683, "ESCHERICHIA COLI", 20_000_001),
        (eid(4), 3040209, 0, "ENTEROCOCCUS SP.", 20_000_001),  # organism without a mapping
        (eid(5), 3023368, None, "NO GROWTH.", 2 * 10**12 + 1),  # date only: the visit of its day
    ]  # fmt: skip
    assert (df["type_concept_id"] == c.LAB).all()


def test_susceptibilities_point_at_their_isolate():
    df = events().filter(pl.col("event_link_id").is_not_null())
    assert df.select("event_id", "concept_id", "value_as_concept_id", "value_as_number", "operator_concept_id",
                     "event_link_id", "event_link_field_concept_id").rows() == [
        (eid(2), 3007481, 45878594, 32.0, c.OPERATORS[">="], eid(2, microbiology.ISOLATE_K), 1147138),
        (eid(3), 3010548, 45884178, 0.25, c.OPERATORS["<="], eid(2, microbiology.ISOLATE_K), 1147138),
    ]  # fmt: skip
    # one record per source row has k = 0: the rows are accounted for once each
    assert events().filter(pl.col("event_id") % 10 == 0).height == 5


def test_specimens_and_their_links_to_test_results():
    spec = microbiology.specimens(MICRO, VISITS, DETAILS, SPECIMENS).collect().sort("specimen_id")
    assert spec.select("specimen_id", "specimen_concept_id", "specimen_date", "specimen_source_value", "visit_occurrence_id").rows() == [
        (501, 4001225, date(2150, 1, 2), "BLOOD CULTURE", 20_000_001),
        (502, 4046280, date(2150, 1, 2), "URINE", 20_000_001),
        (503, 4001225, date(2150, 2, 1), "BLOOD CULTURE", 2 * 10**12 + 1),
    ]  # fmt: skip
    results = (
        events()
        .filter(pl.col("event_link_id").is_null())
        .select(id="event_id", domain_concept_id=pl.lit(c.DOMAIN_CONCEPT["Measurement"]))
    )
    links = microbiology.specimen_links(MICRO, results.lazy()).collect()
    has = links.filter(pl.col("relationship_concept_id") == c.HAS_SPECIMEN)
    assert sorted(has.select("fact_id_1", "fact_id_2").rows()) == [
        (eid(1), 501), (eid(2, microbiology.ISOLATE_K), 502), (eid(4), 502), (eid(5), 503),
    ]  # fmt: skip
    assert links.height == 2 * has.height  # and the reverse, 'Specimen of'
