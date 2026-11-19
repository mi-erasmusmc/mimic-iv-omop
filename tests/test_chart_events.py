from datetime import date
from pathlib import Path

import polars as pl
import pytest
from conftest import admissions, cvb, icustays, no_transfers, ts

from mimic_omop import concepts as c
from mimic_omop.etl import chart_events, omr
from mimic_omop.etl.events import PREFIX
from mimic_omop.vocab import cvb_lookup, unit_map

VISITS = pl.LazyFrame(
    {
        "person_id": [1],
        "visit_occurrence_id": [20_000_001],
        "visit_start_date": [date(2150, 1, 1)],
        "visit_start_datetime": [ts("2150-01-01 08:00")],
        "visit_end_date": [date(2150, 1, 9)],
        "visit_end_datetime": [ts("2150-01-09 12:00")],
    }
)
DETAILS = pl.LazyFrame(
    {
        "visit_occurrence_id": [20_000_001],
        "visit_detail_id": [30_000_001],
        "visit_detail_start_datetime": [ts("2150-01-02 00:00")],
        "visit_detail_end_datetime": [ts("2150-01-05 00:00")],
    }
)
ICUSTAYS = icustays((30_000_001, 20_000_001, "2150-01-02 00:00", "2150-01-05 00:00"))
MAP_SCHEMA = {
    "source_code": pl.String,
    "source_concept_id": pl.Int64,
    "target_concept_id": pl.Int64,
    "target_domain_id": pl.String,
}
ITEMS = pl.LazyFrame(
    [("220045", 2_061_000_001, 3027018, "Measurement"), ("220048", 2_061_000_002, 3022318, "Measurement")],
    schema=MAP_SCHEMA,
    orient="row",
)  # fmt: skip
VALUES = pl.LazyFrame([("AF (Atrial Fibrillation)", 2_061_000_100, 313217, "Meas Value")], schema=MAP_SCHEMA, orient="row")  # fmt: skip
UNITS = pl.LazyFrame({"unit_source_value": ["bpm", "mL"], "unit_concept_id": [8541, 8587]})


def chart_rows(*rows: dict) -> pl.LazyFrame:
    defaults = {
        "subject_id": 1, "hadm_id": 20_000_001, "stay_id": 30_000_001, "itemid": 220045,
        "charttime": ts("2150-01-02 10:00"), "storetime": ts("2150-01-02 10:05"),
        "value": "80", "valuenum": 80.0, "valueuom": "bpm",
    }  # fmt: skip
    schema = {
        "subject_id": pl.Int64, "hadm_id": pl.Int64, "stay_id": pl.Int64, "itemid": pl.Int64,
        "charttime": pl.Datetime("us"), "storetime": pl.Datetime("us"), "value": pl.String,
        "valuenum": pl.Float64, "valueuom": pl.String,
    }  # fmt: skip
    return pl.LazyFrame([defaults | r for r in rows], schema=schema)


def charted(*rows: dict) -> pl.DataFrame:
    return (
        chart_events.charted(chart_rows(*rows), VISITS, DETAILS, ICUSTAYS, ITEMS, VALUES, UNITS)
        .collect()
        .sort("event_id")
    )


def test_numeric_chart_values_with_units_and_care_unit():
    e = charted({}).row(0, named=True)
    assert e["event_id"] == 9 * 10**15 + (30_000_001 * 10**6) * 10
    assert (e["concept_id"], e["domain_id"], e["value_as_number"], e["unit_concept_id"]) == (
        3027018,
        "Measurement",
        80.0,
        8541,
    )
    assert (e["visit_occurrence_id"], e["visit_detail_id"], e["type_concept_id"]) == (
        20_000_001,
        30_000_001,
        c.EHR,
    )


def test_rows_charted_just_outside_their_icu_stay_keep_it():
    # an ED stay before the ICU stay 30_000_001 (2150-01-02 00:00 to 01-05 00:00)
    details = pl.concat(
        [
            DETAILS,
            pl.LazyFrame(
                {
                    "visit_occurrence_id": [20_000_001],
                    "visit_detail_id": [30_000_000],
                    "visit_detail_start_datetime": [ts("2150-01-01 08:00")],
                    "visit_detail_end_datetime": [ts("2150-01-02 00:00")],
                }
            ),
        ]
    )
    rows = chart_rows(
        {"charttime": ts("2150-01-01 21:00")},  # 3 hours before: the ICU stay
        {"charttime": ts("2150-01-01 12:00")},  # 12 hours before: the ED stay covering it
        {"charttime": ts("2150-01-05 00:30")},  # 30 minutes after: the ICU stay
        {"charttime": ts("2150-01-05 03:00")},  # 3 hours after: no care unit stay covers it
    )
    df = chart_events.charted(rows, VISITS, details, ICUSTAYS, ITEMS, VALUES, UNITS).collect()
    got = dict(zip(df["start_datetime"], df["visit_detail_id"], strict=True))
    assert got == {
        ts("2150-01-01 21:00"): 30_000_001,
        ts("2150-01-01 12:00"): 30_000_000,
        ts("2150-01-05 00:30"): 30_000_001,
        ts("2150-01-05 03:00"): None,
    }


def test_text_chart_values_keep_their_text_and_mapped_value_concept():
    df = charted(
        # a mapped rhythm item with a mapped value
        {"itemid": 220048, "value": "AF (Atrial Fibrillation)", "valuenum": None, "valueuom": None},
        # an unmapped item with text only: an observation
        {"itemid": 227969, "value": "Bed in low position", "valuenum": None, "valueuom": None,
         "charttime": ts("2150-01-02 11:00")},
        # an unmapped item with a number: a measurement
        {"itemid": 999999, "value": "3", "valuenum": 3.0, "valueuom": None, "charttime": ts("2150-01-02 12:00")},
    )  # fmt: skip
    assert df.select(
        "domain_id", "concept_id", "value_as_concept_id", "value_as_string"
    ).rows() == [
        ("Measurement", 3022318, 313217, "AF (Atrial Fibrillation)"),
        ("Observation", 0, None, "Bed in low position"),
        ("Measurement", 0, None, None),
    ]


def test_outputs_are_measurements_with_units():
    out = pl.LazyFrame(
        [{"subject_id": 1, "hadm_id": 20_000_001, "stay_id": 30_000_001, "itemid": 226559,
          "charttime": ts("2150-01-02 10:00"), "storetime": ts("2150-01-02 10:05"), "value": 250.0, "valueuom": "mL"}]
    )  # fmt: skip
    items = pl.LazyFrame(
        [("226559", 2_061_000_003, 3014315, "Measurement")], schema=MAP_SCHEMA, orient="row"
    )
    e = (
        chart_events.outputs(out, VISITS, DETAILS, ICUSTAYS, items, UNITS)
        .collect()
        .row(0, named=True)
    )
    assert (e["concept_id"], e["value_as_number"], e["unit_concept_id"]) == (3014315, 250.0, 8587)
    assert e["event_id"] == 10 * 10**15 + (30_000_001 * 10**6) * 10


OMR_MAP = pl.LazyFrame(
    [
        ("Blood Pressure", "systolic", 0, 3004249, "Measurement", 8876),
        ("Blood Pressure", "diastolic", 1, 3012888, "Measurement", 8876),
        ("Weight (Lbs)", "value", 0, 3025315, "Measurement", 8739),
    ],
    schema={"result_name": pl.String, "component": pl.String, "k": pl.Int64, "target_concept_id": pl.Int64,
            "target_domain_id": pl.String, "unit_concept_id": pl.Int64},
    orient="row",
)  # fmt: skip


def test_omr_blood_pressure_is_split_and_unknown_results_kept():
    rows = pl.LazyFrame(
        [
            {"subject_id": 1, "chartdate": date(2150, 1, 3), "seq_num": 1, "result_name": "Blood Pressure", "result_value": "120/80"},
            {"subject_id": 1, "chartdate": date(2150, 3, 1), "seq_num": 1, "result_name": "Weight (Lbs)", "result_value": "170.5"},
            {"subject_id": 1, "chartdate": date(2150, 3, 1), "seq_num": 1, "result_name": "eGFR", "result_value": ">60"},
        ]
    )  # fmt: skip
    df = omr.omr_events(rows, VISITS, OMR_MAP).collect().sort("event_id")
    assert df.select("concept_id", "value_as_number", "unit_concept_id", "visit_occurrence_id").rows() == [
        (3004249, 120.0, 8876, 20_000_001),  # inside the admission's dates
        (3012888, 80.0, 8876, 20_000_001),
        (3025315, 170.5, 8739, None),  # no visit covers this day in these test visits
        (0, None, None, None),  # eGFR: not in the local mapping
    ]  # fmt: skip
    assert df["start_datetime"].is_null().all()


def test_omr_days_outside_visits_get_outpatient_visits():
    from mimic_omop.etl.visit_occurrence import visit_occurrence

    admit = cvb_lookup(cvb(), "x")
    days = pl.LazyFrame(
        {
            "person_id": [1, 1, 1],
            "day": [date(2150, 1, 3), date(2150, 3, 1), date(2150, 3, 1)],
            "source": "omr",
        }
    )
    df = visit_occurrence(
        admissions({}), no_transfers(), admit, admit, outpatient_days=days
    ).collect()
    clinic = df.filter(pl.col("visit_concept_id") == c.OUTPATIENT)
    day = (date(2150, 3, 1) - date(1970, 1, 1)).days
    assert clinic.select("visit_occurrence_id", "visit_start_datetime", "visit_end_datetime").rows() == [
        (2 * 10**12 + 10**5 + day, ts("2150-03-01 00:00"), ts("2150-03-01 23:59:59.999999")),
    ]  # fmt: skip


def test_unit_map_falls_back_to_unambiguous_case_insensitive_ucum():
    concept = pl.LazyFrame(
        [(8587, "mL"), (8550, "ms"), (9999, "mS"), (8840, "mg/dL")],
        schema={"concept_id": pl.Int64, "concept_code": pl.String},
        orient="row",
    ).with_columns(
        vocabulary_id=pl.lit("UCUM"),
        standard_concept=pl.lit("S"),
        invalid_reason=pl.lit(None, pl.String),
    )
    units = dict(unit_map(cvb(), concept).collect().iter_rows())
    assert units["ml"] == 8587 and units["mg/dl"] == 8840  # written in lower case
    assert units["ms"] == 8550 and units["mS"] == 9999  # exact codes still exact
    assert "MS" not in units  # ambiguous: no guess


VOCAB = Path(__file__).parents[1] / "data" / "vocab"


@pytest.mark.skipif(not (VOCAB / "athena" / "concept.parquet").exists(), reason="needs Athena")
def test_every_code_of_the_local_omr_mapping_resolves():
    resolved = omr.omr_map(
        omr.local_mapping(), pl.scan_parquet(VOCAB / "athena" / "concept.parquet")
    )
    missing = resolved.filter(
        pl.col("target_concept_id").is_null() | pl.col("unit_concept_id").is_null()
    ).collect()
    assert missing.is_empty(), missing


def test_icu_procedures_keep_their_start_and_end():
    rows = pl.LazyFrame(
        [{"subject_id": 1, "hadm_id": 20_000_001, "stay_id": 30_000_001, "itemid": 225792, "orderid": 7,
          "starttime": ts("2150-01-02 10:00"), "endtime": ts("2150-01-04 06:00"), "value": 2640.0, "valueuom": "min"},
         {"subject_id": 1, "hadm_id": 20_000_001, "stay_id": 30_000_001, "itemid": 225401, "orderid": 8,
          "starttime": ts("2150-01-01 23:00"), "endtime": ts("2150-01-01 23:01"), "value": 1.0, "valueuom": None}]
    )  # fmt: skip
    items = pl.LazyFrame(
        [("225792", 2_061_000_004, 4230167, "Procedure"), ("225401", 2_061_000_005, 4107893, "Measurement")],
        schema=MAP_SCHEMA,
        orient="row",
    )  # fmt: skip
    df = chart_events.procedures(rows, VISITS, DETAILS, ICUSTAYS, items).collect().sort("event_id")
    assert df.select("event_id", "domain_id", "start_datetime", "end_datetime", "visit_detail_id").rows() == [
        # a culture sent an hour before ICU arrival: a measurement, attached to the ICU stay (R30)
        (13 * 10**15 + (30_000_001 * 10**5) * 10, "Measurement", ts("2150-01-01 23:00"), ts("2150-01-01 23:01"), 30_000_001),
        (13 * 10**15 + (30_000_001 * 10**5 + 1) * 10, "Procedure", ts("2150-01-02 10:00"), ts("2150-01-04 06:00"), 30_000_001),
    ]  # fmt: skip


def test_icu_dates_become_one_record_per_stay_item_and_date():
    details = pl.LazyFrame(
        {"visit_occurrence_id": [20_000_001] * 2, "visit_detail_id": [30_000_001, 30_000_002],
         "visit_detail_start_datetime": [ts("2150-01-01 08:00"), ts("2150-01-02 06:00")],
         "visit_detail_end_datetime": [ts("2150-01-02 06:00"), ts("2150-01-05 00:00")]}
    )  # fmt: skip
    items = pl.LazyFrame([("224184", 2_061_000_010, 4_000_001, "Procedure")], schema=MAP_SCHEMA, orient="row")  # fmt: skip
    picc = {"itemid": 224184, "value": "2150-01-02 00:00:00", "valueuom": "Date"}
    rows = pl.LazyFrame(
        [
            # a PICC line inserted on 2 January, charted again every shift
            picc | {"charttime": ts("2150-01-02 14:00"), "storetime": ts("2150-01-02 14:05")},
            picc | {"charttime": ts("2150-01-03 08:00"), "storetime": ts("2150-01-03 08:02")},
            picc | {"charttime": ts("2150-01-04 08:00"), "storetime": ts("2150-01-04 08:01")},
            # and in the next ICU stay: still the same insertion
            picc | {"charttime": ts("2150-01-06 08:00"), "storetime": ts("2150-01-06 08:01"), "stay_id": 40_000_002},
            # an unmapped item with a mistyped date a month after it was recorded
            {"itemid": 224288, "value": "2150-02-03 10:00:00", "valueuom": "Date and Time",
             "charttime": ts("2150-01-03 10:00"), "storetime": ts("2150-01-03 10:05")},
        ],
        schema={"itemid": pl.Int64, "value": pl.String, "valueuom": pl.String,
                "charttime": pl.Datetime("us"), "storetime": pl.Datetime("us"), "stay_id": pl.Int64},
    ).with_columns(pl.lit(1).alias("subject_id"), pl.lit(20_000_001).alias("hadm_id"), pl.col("stay_id").fill_null(40_000_001))  # fmt: skip
    df = chart_events.dated(rows, VISITS, details, items).collect().sort("event_id")
    key = PREFIX["datetimeevents"] * 10**15 + 40_000_001 * 10**6 * 10
    assert df.select("event_id", "concept_id", "domain_id", "start_datetime", "visit_detail_id", "value_source_value").rows() == [
        # its date without a time, in the care unit of its first charting that day
        (key, 4_000_001, "Procedure", None, 30_000_002, "2150-01-02 00:00:00"),
        # a date cannot be later than its recording
        (key + 10, 0, "Procedure", ts("2150-01-03 10:05"), 30_000_002, "2150-02-03 10:00:00"),
    ]  # fmt: skip
    assert df["start_date"].to_list() == [date(2150, 1, 2), date(2150, 1, 3)]
    assert (df["visit_occurrence_id"] == 20_000_001).all()
