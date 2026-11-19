import polars as pl
from conftest import ts

from mimic_omop import concepts as c
from mimic_omop.etl import ed, omr
from mimic_omop.etl.events import PREFIX

EDSTAYS = pl.LazyFrame(
    {
        "subject_id": [1, 2],
        "stay_id": [30_000_100, 30_000_200],
        "intime": [ts("2150-01-01 08:00"), ts("2150-01-05 09:00")],
    }
)
# the ED transfer of person 1; person 2 is not in MIMIC-IV, so has none
DETAILS = pl.LazyFrame(
    {
        "person_id": [1],
        "visit_detail_id": [35_000_001],
        "visit_occurrence_id": [35_000_001],
        "visit_detail_start_datetime": [ts("2150-01-01 08:00")],
        "visit_detail_end_datetime": [ts("2150-01-01 14:00")],
    }
)
VISITS = pl.LazyFrame(
    {
        "person_id": [1],
        "visit_occurrence_id": [35_000_001],
        "visit_start_datetime": [ts("2150-01-01 08:00")],
        "visit_end_datetime": [ts("2150-01-01 14:00")],
    }
)
CONCEPT = pl.LazyFrame(
    [(3020891, "LOINC", "8310-5", "Measurement", "S"), (3027018, "LOINC", "8867-4", "Measurement", "S"),
     (43055141, "LOINC", "72514-3", "Measurement", "S"), (3019237, "LOINC", "8661-1", "Observation", "S"),
     (9289, "UCUM", "[degF]", "Unit", "S"), (8541, "UCUM", "/min", "Unit", "S")],
    schema=["concept_id", "vocabulary_id", "concept_code", "domain_id", "standard_concept"],
    orient="row",
).with_columns(invalid_reason=pl.lit(None, pl.String))  # fmt: skip


def links() -> pl.LazyFrame:
    return ed.stay_links(EDSTAYS, DETAILS)


def eid(source: str, key: int, k: int = 0) -> int:
    return PREFIX[source] * 10**15 + key * 10 + k


def test_records_join_the_visit_of_their_ed_transfer_and_unknown_patients_are_left_out():
    assert links().collect().select("stay_id", "ed_visit", "ed_detail").rows() == [
        (30_000_100, 35_000_001, 35_000_001)
    ]


def test_triage_values_become_one_record_each_at_ed_arrival():
    triage = pl.LazyFrame(
        {"subject_id": [1, 2], "stay_id": [30_000_100, 30_000_200], "temperature": [98.4, 99.0],
         "heartrate": [None, 80.0], "resprate": [16.0, None], "o2sat": [None, None], "sbp": [None, None],
         "dbp": [None, None], "pain": ["unable", "3"], "acuity": [2.0, 3.0], "chiefcomplaint": ["Abd pain", "Fall"]}
    )  # fmt: skip
    mapping = omr.omr_map(ed.local_mapping(), CONCEPT)
    df = ed.triage(triage, links(), mapping).collect().sort("event_id")
    assert df.select("event_id", "source_value", "concept_id", "value_as_number", "value_source_value",
                     "value_as_string", "unit_concept_id", "start_datetime").rows() == [
        (eid("ed_triage", 30_000_100 * 10 + 0), "temperature", 3020891, 98.4, "98.4", None, 9289, ts("2150-01-01 08:00")),
        # resprate's LOINC code is not in this test's concept table; its unit is
        (eid("ed_triage", 30_000_100 * 10 + 2), "resprate", 0, 16.0, "16.0", None, 8541, ts("2150-01-01 08:00")),
        (eid("ed_triage", 30_000_100 * 10 + 6), "pain", 43055141, None, "unable", None, None, ts("2150-01-01 08:00")),
        (eid("ed_triage", 30_000_100 * 10 + 7), "acuity", 0, 2.0, "2.0", None, None, ts("2150-01-01 08:00")),
        (eid("ed_triage", 30_000_100 * 10 + 8), "chiefcomplaint", 3019237, None, "Abd pain", "Abd pain", None, ts("2150-01-01 08:00")),
    ]  # fmt: skip
    assert (df["visit_detail_id"] == 35_000_001).all()


def test_gsn_crosswalk_maps_to_the_clinical_drug():
    prescriptions = pl.LazyFrame(
        {"gsn": ["000018", "000018", "000018 000019", "0"], "ndc": ["A", "B", "B", "C"]}
    )
    ndc_map = pl.LazyFrame({"source_code": ["A", "B", "C"], "target_concept_id": [100, 200, 300]})
    concept = pl.LazyFrame(
        {"concept_id": [100, 200, 300, 400], "concept_class_id": ["Clinical Drug", "Branded Drug", "Clinical Drug", "Clinical Drug"],
         "domain_id": ["Drug"] * 4}
    )  # fmt: skip
    # the branded drug 200 descends from the clinical drug 100
    ancestor = pl.LazyFrame(
        {"ancestor_concept_id": [100, 100, 300], "descendant_concept_id": [100, 200, 300]}
    )
    df = ed.gsn_map(prescriptions, ndc_map, concept, ancestor).collect().sort("gsn")
    assert df.rows() == [("000018", 100, "Drug"), ("000019", 100, "Drug")]  # GSN 0 is missing


def test_pyxis_takes_the_first_gsn_that_maps():
    pyxis = pl.LazyFrame(
        {"subject_id": [1, 1, 1], "stay_id": [30_000_100] * 3, "charttime": [ts("2150-01-01 09:00")] * 3,
         "med_rn": [1, 1, 2], "name": ["Albuterol Inhaler", "Albuterol Inhaler", "Unknown"], "gsn_rn": [1, 2, 1],
         "gsn": ["999999", "028090", "0"]}
    )  # fmt: skip
    gsns = pl.LazyFrame(
        {"gsn": ["028090"], "target_concept_id": [1154343], "target_domain_id": ["Drug"]}
    )
    df = ed.pyxis(pyxis, links(), VISITS, DETAILS, gsns).collect().sort("event_id")
    assert df.select("event_id", "concept_id", "source_value", "type_concept_id").rows() == [
        (eid("ed_pyxis", 30_000_100 * ed.ROWS_PER_STAY + 0), 1154343, "Albuterol Inhaler", c.EHR_DISPENSING),
        (eid("ed_pyxis", 30_000_100 * ed.ROWS_PER_STAY + 1), 0, "Unknown", c.EHR_DISPENSING),
    ]  # fmt: skip


def test_medrecon_uses_the_ndc_else_the_gsn_and_one_row_per_medication():
    medrecon = pl.LazyFrame(
        {"subject_id": [1] * 3, "stay_id": [30_000_100] * 3, "charttime": [ts("2150-01-01 08:30")] * 3,
         "name": ["albuterol", "albuterol", "calcium"], "gsn": ["028090", "028090", "001340"],
         "ndc": ["21695042308", "21695042308", "0"], "etc_rn": [1, 2, 1]}
    )  # fmt: skip
    ndc_map = pl.LazyFrame(
        {"source_code": ["21695042308"], "source_concept_id": [45000001], "target_concept_id": [1154343],
         "target_domain_id": ["Drug"]}
    )  # fmt: skip
    gsns = pl.LazyFrame(
        {"gsn": ["001340"], "target_concept_id": [19035704], "target_domain_id": ["Drug"]}
    )
    df = ed.medrecon(medrecon, links(), VISITS, DETAILS, ndc_map, gsns).collect().sort("event_id")
    assert df.select(
        "source_value", "concept_id", "source_concept_id", "type_concept_id"
    ).rows() == [
        (
            "albuterol",
            1154343,
            45000001,
            c.EHR_MEDICATION_LIST,
        ),  # by NDC; the etc_rn 2 repeat is left out
        ("calcium", 19035704, 0, c.EHR_MEDICATION_LIST),  # no NDC: by GSN
    ]
