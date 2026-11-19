import polars as pl
from conftest import icustays, ts

from mimic_omop import concepts as c
from mimic_omop.etl import drug_events

MAP_SCHEMA = {
    "source_code": pl.String,
    "source_concept_id": pl.Int64,
    "target_concept_id": pl.Int64,
    "target_domain_id": pl.String,
}


def code_map(*rows: tuple[str, int, int | None, str | None]) -> pl.LazyFrame:
    return pl.LazyFrame(rows, schema=MAP_SCHEMA, orient="row")


EMPTY = code_map()
NDC_ATHENA = code_map(
    ("00002771001", 45000001, 1001, "Drug"), ("99999999999", 45000002, None, None)
)
DRUG_CVB = code_map(("Insulin", 2_060_000_001, 2001, "Drug"), ("Bag", 2_060_000_002, None, None))
FORMULARY_CVB = code_map(("FENT2I", 2_060_000_003, 3001, "Drug"))
ROUTES = code_map(("IV", 2_060_000_009, 4171047, "Route"))
NO_VISITS = pl.LazyFrame(
    schema={"person_id": pl.Int64, "visit_occurrence_id": pl.Int64,
            "visit_start_datetime": pl.Datetime("us"), "visit_end_datetime": pl.Datetime("us")}
)  # fmt: skip
NO_DETAILS = pl.LazyFrame(
    schema={"visit_occurrence_id": pl.Int64, "visit_detail_id": pl.Int64,
            "visit_detail_start_datetime": pl.Datetime("us"), "visit_detail_end_datetime": pl.Datetime("us")}
)  # fmt: skip


def prescriptions(*rows: dict) -> pl.LazyFrame:
    defaults = {
        "subject_id": 1, "hadm_id": 20_000_001, "pharmacy_id": 1, "poe_seq": 1, "drug_type": "MAIN",
        "drug": "Insulin", "ndc": None, "formulary_drug_cd": None, "route": "IV",
        "starttime": ts("2150-01-02 08:00"), "stoptime": ts("2150-01-03 08:00"),
    }  # fmt: skip
    schema = {
        "subject_id": pl.Int64, "hadm_id": pl.Int64, "pharmacy_id": pl.Int64, "poe_seq": pl.Int64,
        "drug_type": pl.String, "drug": pl.String, "ndc": pl.String, "formulary_drug_cd": pl.String,
        "route": pl.String, "starttime": pl.Datetime("us"), "stoptime": pl.Datetime("us"),
    }  # fmt: skip
    return pl.LazyFrame([defaults | r for r in rows], schema=schema)


def coded_orders(rx: pl.LazyFrame) -> pl.LazyFrame:
    return drug_events.prescription_codes(rx, NDC_ATHENA, EMPTY, DRUG_CVB, FORMULARY_CVB)


def test_order_concept_from_the_first_key_that_maps():
    df = (
        coded_orders(
            prescriptions(
                {"pharmacy_id": 1, "ndc": "00002771001", "drug": "Insulin"},  # NDC via Athena wins
                {
                    "pharmacy_id": 2,
                    "ndc": "99999999999",
                    "drug": "Insulin",
                },  # NDC unmapped: drug name
                {"pharmacy_id": 3, "ndc": "0", "drug": "Fentanyl", "formulary_drug_cd": "FENT2I"},
                {"pharmacy_id": 4, "drug": "Bag"},  # nothing maps
            )
        )
        .collect()
        .sort("pharmacy_id")
    )
    assert df.select("concept_id", "source_concept_id").rows() == [
        (1001, 45000001),
        (2001, 2_060_000_001),
        (3001, 2_060_000_003),
        (0, 0),
    ]


def test_orders_end_at_their_stop_time_but_not_before_their_start():
    rx = prescriptions(
        {"pharmacy_id": 1},
        {"pharmacy_id": 2, "stoptime": ts("2150-01-01 08:00")},  # stops before it starts
        {"pharmacy_id": 3, "stoptime": None},
    )
    df = (
        drug_events.orders(coded_orders(rx), NO_VISITS, NO_DETAILS, ROUTES)
        .collect()
        .sort("event_id")
    )
    assert df.select("end_datetime", "verbatim_end_date").rows() == [
        (ts("2150-01-03 08:00"), ts("2150-01-03").date()),
        (ts("2150-01-02 08:00"), ts("2150-01-01").date()),
        (ts("2150-01-02 08:00"), None),
    ]
    assert (df["type_concept_id"] == c.EHR_ORDER).all()
    assert (df["route_concept_id"] == 4171047).all()
    assert df["visit_occurrence_id"].to_list() == [20_000_001] * 3


def test_only_administered_emar_actions_count():
    actions = pl.Series(
        ["Administered", "Delayed Administered", "Administered in Other Location", "Flushed",
         "Not Given", "Hold Dose", "Assessed", "Stopped", "Not Given per Sliding Scale", None, "Something New"]
    )  # fmt: skip
    given = pl.select(drug_events.administered(pl.lit(actions))).to_series().to_list()
    assert given == [True, True, True, True, False, False, False, False, False, False, False]


# administration detail: a row of its own (no parent_field_ordinal) and one per product given
EMAR_DETAIL = pl.LazyFrame(
    [("1-1", None, None, None), ("1-1", "1.1", " 2 ", "mL"),
     ("1-2", None, None, None), ("1-2", "1.1", "1", "TAB"), ("1-2", "2.1", "1", "TAB")],
    schema=["emar_id", "parent_field_ordinal", "product_amount_given", "product_unit"],
    orient="row",
)  # fmt: skip


def test_emar_takes_the_linked_order_concept_else_the_medication_name():
    rx = prescriptions(
        {"pharmacy_id": 1, "drug_type": "MAIN", "ndc": "00002771001"},
        {"pharmacy_id": 1, "drug_type": "BASE", "drug": "Bag"},  # the diluent of the same order
    )
    emar = pl.LazyFrame(
        [
            {"subject_id": 1, "hadm_id": 20_000_001, "emar_id": "1-1", "emar_seq": 1, "pharmacy_id": 1,
             "medication": "Insulin Human Regular", "event_txt": "Administered", "charttime": ts("2150-01-02 09:00")},
            {"subject_id": 1, "hadm_id": 20_000_001, "emar_id": "1-2", "emar_seq": 2, "pharmacy_id": None,
             "medication": "Insulin", "event_txt": "Administered", "charttime": ts("2150-01-02 10:00")},
            {"subject_id": 1, "hadm_id": 20_000_001, "emar_id": "1-3", "emar_seq": 3, "pharmacy_id": 1,
             "medication": "Insulin", "event_txt": "Not Given", "charttime": ts("2150-01-02 11:00")},
        ],
        schema_overrides={"pharmacy_id": pl.Int64},
    )  # fmt: skip
    df = (
        drug_events.emar_administrations(
            emar, EMAR_DETAIL, coded_orders(rx), NO_VISITS, NO_DETAILS, ROUTES, DRUG_CVB, EMPTY
        )
        .collect()
        .sort("event_id")
    )
    assert df.select("event_id", "concept_id", "route_concept_id", "start_datetime", "end_datetime",
                     "quantity", "dose_unit_source_value").rows() == [
        (7 * 10**15 + (1 * 10**6 + 1) * 10, 1001, 4171047, ts("2150-01-02 09:00"), ts("2150-01-02 09:00"), 2.0, "mL"),
        # two products: no quantity of one drug concept
        (7 * 10**15 + (1 * 10**6 + 2) * 10, 2001, None, ts("2150-01-02 10:00"), ts("2150-01-02 10:00"), None, None),
    ]  # fmt: skip
    assert (df["type_concept_id"] == c.EHR_ADMINISTRATION).all()


def test_rewritten_icu_inputs_are_left_out():
    inputs = pl.LazyFrame(
        [
            {"subject_id": 1, "hadm_id": 20_000_001, "stay_id": 30_000_001, "itemid": 225798, "orderid": 1,
             "starttime": ts("2150-01-02 08:00"), "endtime": ts("2150-01-02 09:00"), "statusdescription": s}
            for s in ["FinishedRunning", "ChangeDose/Rate", "Rewritten"]
        ]
    )  # fmt: skip
    item_map = code_map(("225798", 2_060_000_010, 1_000_001, "Drug"))
    df = drug_events.icu_inputs(inputs, NO_VISITS, NO_DETAILS, icustays(), item_map).collect()
    assert df.height == 2 and df["event_id"].is_unique().all()
    assert (df["end_datetime"] == ts("2150-01-02 09:00")).all()


def test_discharge_medications_and_orders_without_start():
    rx = prescriptions(
        {"pharmacy_id": 1, "starttime": None, "stoptime": None},  # discharge medication
        {"pharmacy_id": 2, "starttime": None},  # unit dose order without a start time
    )
    pharmacy = pl.LazyFrame(
        {
            "pharmacy_id": [1, 2],
            "proc_type": ["Discharge Med", "Unit Dose"],
            "starttime": [None, None],
            "entertime": [ts("2150-01-05 10:00"), ts("2150-01-02 09:00")],
        },
        schema_overrides={"starttime": pl.Datetime("us")},
    )
    coded = drug_events.prescription_codes(rx, NDC_ATHENA, EMPTY, DRUG_CVB, FORMULARY_CVB, pharmacy)
    df = drug_events.orders(coded, NO_VISITS, NO_DETAILS, ROUTES).collect().sort("start_datetime")
    assert df.select("type_concept_id", "start_datetime", "end_datetime").rows() == [
        (c.EHR_ORDER, ts("2150-01-02 09:00"), ts("2150-01-03 08:00")),
        (c.EHR_PRESCRIPTION, ts("2150-01-05 10:00"), ts("2150-02-03 10:00")),  # + 29 days
    ]
