import polars as pl
from conftest import transfers, ts

from mimic_omop import concepts as c
from mimic_omop.etl.visit_detail import visit_detail

CARE_SITES = pl.LazyFrame(
    {"care_site_id": [7], "care_site_source_value": ["Medicine"]},
    schema={"care_site_id": pl.Int64, "care_site_source_value": pl.String},
)


def details(tr: pl.LazyFrame) -> pl.DataFrame:
    return visit_detail(tr, CARE_SITES).collect().sort("visit_detail_id")


def test_unit_concepts():
    units = [
        "Emergency Department",
        "Emergency Department Observation",
        "Medical Intensive Care Unit (MICU)",
        "Trauma SICU (TSICU)",
        "Coronary Care Unit (CCU)",
        "Observation",
        "Medicine",
        "Discharge Lounge",
    ]
    df = details(transfers(*[{"transfer_id": i, "careunit": u} for i, u in enumerate(units)]))
    assert df["visit_detail_concept_id"].to_list() == [
        c.EMERGENCY_ROOM,
        c.EMERGENCY_ROOM,
        c.INTENSIVE_CARE,
        c.INTENSIVE_CARE,
        c.INTENSIVE_CARE,
        c.OBSERVATION_ROOM,
        c.INPATIENT,
        c.INPATIENT,
    ]


def test_stays_within_an_admission_are_chained_and_discharge_events_dropped():
    df = details(
        transfers(
            {"transfer_id": 1, "intime": ts("2150-01-01 10:00"), "outtime": ts("2150-01-02")},
            {"transfer_id": 2, "eventtype": "transfer", "careunit": "Surgical Intensive Care Unit (SICU)",
             "intime": ts("2150-01-02"), "outtime": ts("2150-01-04")},
            {"transfer_id": 3, "eventtype": "discharge", "careunit": None,
             "intime": ts("2150-01-04"), "outtime": None},
        )
    )  # fmt: skip
    assert df.select(
        "visit_detail_id", "visit_occurrence_id", "preceding_visit_detail_id", "care_site_id"
    ).rows() == [(1, 100, None, 7), (2, 100, 1, None)]


def test_unadmitted_stays_point_at_their_ed_visit():
    ed = {"hadm_id": None, "eventtype": "ED", "careunit": "Emergency Department"}
    df = details(
        transfers(
            ed | {"transfer_id": 31, "intime": ts("2150-03-01 10:00"), "outtime": ts("2150-03-01 14:00")},
            ed | {"transfer_id": 32, "intime": ts("2150-03-01 14:00"), "outtime": ts("2150-03-02 09:00")},
        )
    )  # fmt: skip
    assert df["visit_occurrence_id"].to_list() == [31, 31]
    assert df["preceding_visit_detail_id"].to_list() == [None, 31]


def test_the_preceding_stay_is_the_persons_previous_one_across_visits():
    ed = {"hadm_id": None, "eventtype": "ED", "careunit": "Emergency Department"}
    df = details(
        transfers(
            {"transfer_id": 1, "intime": ts("2150-01-01 10:00"), "outtime": ts("2150-01-02")},
            {"transfer_id": 2, "eventtype": "transfer", "intime": ts("2150-01-02"), "outtime": ts("2150-01-04")},
            ed | {"transfer_id": 3, "intime": ts("2150-02-01 10:00"), "outtime": ts("2150-02-01 14:00")},
            {"transfer_id": 4, "hadm_id": 101, "intime": ts("2150-03-01 10:00"), "outtime": ts("2150-03-03")},
            {"transfer_id": 5, "subject_id": 2, "hadm_id": 200, "intime": ts("2150-01-03"), "outtime": ts("2150-01-05")},
        )
    )  # fmt: skip
    assert df.select("visit_detail_id", "preceding_visit_detail_id").rows() == [
        (1, None),
        (2, 1),
        (3, 2),  # an ED-only visit after the admission
        (4, 3),  # the next admission's first stay
        (5, None),  # another person's stays are never linked
    ]


def test_a_stay_without_outtime_ends_at_the_next_transfer():
    df = details(
        transfers(
            {"transfer_id": 1, "careunit": "Medical Intensive Care Unit (MICU)",
             "intime": ts("2150-01-01 10:00"), "outtime": None},
            {"transfer_id": 2, "eventtype": "transfer", "intime": ts("2150-01-03"), "outtime": ts("2150-01-04")},
            {"transfer_id": 3, "eventtype": "transfer", "intime": ts("2150-01-04"), "outtime": None},  # last stay
            {"transfer_id": 4, "eventtype": "discharge", "careunit": None, "intime": ts("2150-01-05"), "outtime": None},
        )
    )  # fmt: skip
    assert df.select("visit_detail_id", "visit_detail_end_datetime").rows() == [
        (1, ts("2150-01-03")),
        (2, ts("2150-01-04")),
        (3, ts("2150-01-05")),  # the discharge event's time
    ]
