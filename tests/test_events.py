from datetime import date

import polars as pl
from conftest import ts

from mimic_omop import concepts as c
from mimic_omop.etl import events

ICD_MAP = pl.LazyFrame(
    [
        # code mapping to a condition and an observation; history code with a value; unmapped
        ("ICD10CM", "E119", 1, 100, "Condition", None),
        ("ICD10CM", "E119", 1, 101, "Observation", None),
        ("ICD10CM", "Z8673", 2, 300, "Observation", 301),
        ("ICD10CM", "R99", 3, None, None, None),
    ],
    schema={
        "vocabulary_id": pl.String, "source_code": pl.String, "source_concept_id": pl.Int64,
        "target_concept_id": pl.Int64, "target_domain_id": pl.String, "value_as_concept_id": pl.Int64,
    },
    orient="row",
)  # fmt: skip
VISITS = pl.LazyFrame(
    {
        "visit_occurrence_id": [20_000_001],
        "visit_start_date": [date(2150, 1, 1)],
        "visit_start_datetime": [ts("2150-01-01 08:00")],
        "visit_end_date": [date(2150, 1, 9)],
    }
)


def diagnoses(*rows: tuple[int, str], version: int = 10) -> pl.DataFrame:
    dx = pl.LazyFrame(
        [{"subject_id": 1, "hadm_id": 20_000_001, "seq_num": s, "icd_code": code, "icd_version": version} for s, code in rows]
    )  # fmt: skip
    return events.diagnoses(dx, VISITS, ICD_MAP).collect().sort("event_id")


def test_diagnoses_are_routed_by_target_domain_with_stable_ids():
    df = diagnoses((1, "E119"), (2, "Z8673"), (3, "R99"), (4, "NOTACODE"))
    # key (hadm_id * 1000 + seq_num) * 10 + 1 for ICD-10, then k
    base = 1 * 10**15 + 20_000_001 * 10**5
    assert df.select("event_id", "concept_id", "domain_id", "value_as_concept_id", "source_concept_id").rows() == [
        (base + 110, 100, "Condition", None, 1),
        (base + 111, 101, "Observation", None, 1),
        (base + 210, 300, "Observation", 301, 2),
        (base + 310, 0, "Condition", None, 3),
        (base + 410, 0, "Condition", None, 0),
    ]  # fmt: skip
    assert (df["start_date"] == date(2150, 1, 1)).all()
    assert (df["type_concept_id"] == c.EHR_BILLING).all()


def test_condition_status_from_sequence_number():
    df = diagnoses((1, "E119"), (2, "R99"))
    status = dict(zip(df["event_id"] % 1000, df["status_concept_id"], strict=True))
    assert status == {110: c.PRIMARY_DIAGNOSIS, 111: None, 210: c.SECONDARY_DIAGNOSIS}


def test_admissions_coded_in_both_icd_versions_keep_one_key_per_row():
    # some MIMIC-IV admissions are coded in ICD-9 and ICD-10 with the same seq_num
    both = pl.concat([diagnoses((1, "R99"), version=9), diagnoses((1, "R99"))])
    assert both["event_id"].is_unique().all()
    assert (both["event_id"] % 10 == 0).all()  # each row is its own k = 0


def test_diagnosis_without_visit_keeps_a_null_date_to_fail_validation():
    dx = pl.LazyFrame(
        [{"subject_id": 1, "hadm_id": 1, "seq_num": 1, "icd_code": "R99", "icd_version": 10}]
    )
    assert events.diagnoses(dx, VISITS, ICD_MAP).collect()["start_date"].to_list() == [None]


def test_hcpcs_vocabulary_by_code_shape():
    hcpcs_map = pl.LazyFrame(
        [
            ("CPT4", "93454", 7, 70, "Procedure", None),
            ("HCPCS", "G0378", 8, 80, "Observation", None),
        ],
        schema=ICD_MAP.collect_schema(),
        orient="row",
    )
    ev = pl.LazyFrame(
        [{"subject_id": 1, "hadm_id": 2, "seq_num": i, "chartdate": date(2150, 1, 2), "hcpcs_cd": code}
         for i, code in enumerate(["93454", "G0378 "])]
    )  # fmt: skip
    df = events.hcpcs(ev, hcpcs_map).collect().sort("event_id")
    assert df.select("concept_id", "domain_id", "source_value").rows() == [
        (70, "Procedure", "93454"),
        (80, "Observation", "G0378"),
    ]


def lab_rows(*rows: dict) -> pl.LazyFrame:
    defaults = {
        "labevent_id": 1, "subject_id": 1, "hadm_id": None, "itemid": 50912,
        "charttime": ts("2150-01-02 10:00"), "value": "1.1", "valuenum": 1.1, "valueuom": "mg/dL",
        "ref_range_lower": 0.5, "ref_range_upper": 1.2,
    }  # fmt: skip
    schema = {
        "labevent_id": pl.Int64, "subject_id": pl.Int64, "hadm_id": pl.Int64, "itemid": pl.Int64,
        "charttime": pl.Datetime("us"), "value": pl.String, "valuenum": pl.Float64,
        "valueuom": pl.String, "ref_range_lower": pl.Float64, "ref_range_upper": pl.Float64,
    }  # fmt: skip
    return pl.LazyFrame([defaults | r for r in rows], schema=schema)


LAB_VISITS = pl.LazyFrame(
    {
        "person_id": [1, 1],
        "visit_occurrence_id": [20_000_001, 1_000_000_000_000],
        "visit_start_datetime": [ts("2150-01-01 08:00"), ts("2150-03-01 09:00")],
        "visit_end_datetime": [ts("2150-01-09 12:00"), ts("2150-03-01 09:00")],
    }
)
LAB_DETAILS = pl.LazyFrame(
    {
        "visit_occurrence_id": [20_000_001],
        "visit_detail_id": [30_000_001],
        "visit_detail_start_datetime": [ts("2150-01-02 00:00")],
        "visit_detail_end_datetime": [ts("2150-01-03 00:00")],
    }
)
LAB_MAP = pl.LazyFrame(
    [("50912", 2_000_000_001, 3016723, "Measurement", None), ("51706", 2_000_000_002, 42530744, "Meas Value", None)],
    schema={"source_code": pl.String, "source_concept_id": pl.Int64, "target_concept_id": pl.Int64,
            "target_domain_id": pl.String, "value_as_concept_id": pl.Int64},
    orient="row",
)  # fmt: skip
UNITS = pl.LazyFrame({"unit_source_value": ["mg/dL"], "unit_concept_id": [8840]})


def labs(*rows: dict) -> pl.DataFrame:
    return (
        events.labs(lab_rows(*rows), LAB_VISITS, LAB_DETAILS, LAB_MAP, UNITS)
        .collect()
        .sort("event_id")
    )


def test_lab_values_units_and_ranges():
    e = labs({}).row(0, named=True)
    assert e["event_id"] == 5 * 10**15 + 10
    assert (e["concept_id"], e["domain_id"], e["type_concept_id"]) == (
        3016723,
        "Measurement",
        c.LAB,
    )
    assert (e["value_as_number"], e["unit_concept_id"], e["range_low"], e["range_high"]) == (
        1.1,
        8840,
        0.5,
        1.2,
    )
    assert (e["source_value"], e["unit_source_value"], e["value_source_value"]) == (
        "50912",
        "mg/dL",
        "1.1",
    )


def test_lab_operator_values_are_split():
    df = labs(
        {"labevent_id": 1, "value": "<0.5", "valuenum": None},
        {"labevent_id": 2, "value": ">= 100", "valuenum": None},
        {"labevent_id": 3, "value": "NEG", "valuenum": None, "valueuom": " "},
    )
    assert df.select("value_as_number", "operator_concept_id", "value_source_value", "unit_source_value").rows() == [
        (0.5, c.OPERATORS["<"], "<0.5", "mg/dL"),
        (100.0, c.OPERATORS[">="], ">= 100", "mg/dL"),
        (None, None, "NEG", None),
    ]  # fmt: skip


def test_labs_are_assigned_to_visits_and_care_units_by_time():
    df = labs(
        {"labevent_id": 1, "hadm_id": 20_000_001},  # in the admission and its care unit
        {"labevent_id": 2, "charttime": ts("2150-01-05 10:00")},  # no hadm_id, inside the admission
        {"labevent_id": 3, "charttime": ts("2150-03-01 09:00")},  # its Laboratory Visit
        {"labevent_id": 4, "charttime": ts("2150-06-01 09:00")},  # no visit at all
    )
    assert df.select("visit_occurrence_id", "visit_detail_id").rows() == [
        (20_000_001, 30_000_001),
        (20_000_001, None),
        (1_000_000_000_000, None),
        (None, None),
    ]


def test_lab_targets_outside_event_domains_fall_back_to_concept_zero():
    e = labs({"itemid": 51706, "value": "HOLD.", "valuenum": None}).row(0, named=True)
    assert (e["concept_id"], e["domain_id"], e["source_concept_id"]) == (
        0,
        "Measurement",
        2_000_000_002,
    )


def test_long_text_results_are_truncated_to_the_cdm_length():
    e = labs({"value": "HOLD.  DISCARD GREATER THAN 4 HOURS OLD. " * 3, "valuenum": None}).row(
        0, named=True
    )
    assert len(e["value_source_value"]) == 50
