from datetime import date

import polars as pl
import pytest

from mimic_eval.cohorts import EVENT_COLUMNS, Data, Unsupported, _collapse, cohort

CODESETS = pl.DataFrame({"codeset_id": [1, 2, 3], "concept_id": [100, 200, 9201]})


def events(*rows: dict) -> pl.LazyFrame:
    defaults = {"event_id": None, "end_date": None, "visit_id": None, "value_as_number": None,
                "unit_concept_id": None}  # fmt: skip
    rows = [defaults | r for r in rows]
    for i, r in enumerate(rows):
        r["event_id"] = r["event_id"] if r["event_id"] is not None else i
    return pl.LazyFrame(rows, schema=EVENT_COLUMNS)


def d(s: str) -> date:
    return date.fromisoformat(s)


def criteria(codeset: int, **extra) -> dict:
    return {"ConditionOccurrence": {"CodesetId": codeset, **extra}}


def definition(**parts) -> dict:
    base = {
        "PrimaryCriteria": {"CriteriaList": [criteria(1)], "PrimaryCriteriaLimit": {"Type": "All"}},
        "CollapseSettings": {"CollapseType": "ERA", "EraPad": 0},
        "EndStrategy": {"DateOffset": {"DateField": "StartDate", "Offset": 0}},
    }
    return base | parts


def data(pool: pl.LazyFrame, visits: pl.LazyFrame | None = None, periods=None) -> Data:
    visits = visits if visits is not None else events()
    ev = {k: pool for k in ("condition", "procedure", "drug", "measurement")} | {"visit": visits}
    return Data(ev, periods)


def rows(df: pl.DataFrame) -> list[tuple]:
    return df.select("person_id", "cohort_start_date", "cohort_end_date").rows()


def test_first_is_the_first_record_of_all_time_even_outside_observation():
    pool = events(
        {"person_id": 1, "concept_id": 100, "start_date": d("2150-01-01")},
        {"person_id": 1, "concept_id": 100, "start_date": d("2150-06-01")},
    )
    periods = pl.LazyFrame(
        {"person_id": [1], "op_start": [d("2150-03-01")], "op_end": [d("2150-12-31")]}
    )
    df = definition(PrimaryCriteria={"CriteriaList": [criteria(1, First=True)]})
    # Circe picks the first record before applying observation periods: it lies outside one
    assert rows(cohort(df, data(pool, periods=periods), CODESETS)) == []
    assert rows(cohort(df, data(pool), CODESETS)) == [(1, d("2150-01-01"), d("2150-01-01"))]


def test_washout_rule_counts_records_in_a_window_before_the_index():
    pool = events(
        {"person_id": 1, "concept_id": 100, "start_date": d("2150-01-01")},
        {"person_id": 1, "concept_id": 100, "start_date": d("2150-01-20")},  # 19 days later
        {"person_id": 2, "concept_id": 100, "start_date": d("2150-01-01")},
    )
    washout = {"Type": "ALL", "CriteriaList": [{
        "Criteria": criteria(1),
        "StartWindow": {"Start": {"Days": 30, "Coeff": -1}, "End": {"Days": 1, "Coeff": -1}},
        "IgnoreObservationPeriod": True, "Occurrence": {"Type": 0, "Count": 0},
    }]}  # fmt: skip
    df = definition(InclusionRules=[{"name": "washout", "expression": washout}])
    assert rows(cohort(df, data(pool), CODESETS)) == [
        (1, d("2150-01-01"), d("2150-01-01")),
        (2, d("2150-01-01"), d("2150-01-01")),
    ]  # the record 19 days after the first is excluded


def test_visit_overlapping_the_index_and_offset_end_capped_by_observation():
    pool = events(
        {"person_id": 1, "concept_id": 100, "start_date": d("2150-01-02")},
        {"person_id": 2, "concept_id": 100, "start_date": d("2150-02-01")},  # no visit
    )
    visits = events(
        {"person_id": 1, "concept_id": 9201, "start_date": d("2150-01-01"), "end_date": d("2150-01-05")},
        {"person_id": 2, "concept_id": 9201, "start_date": d("2150-01-01"), "end_date": d("2150-01-05")},
    )  # fmt: skip
    overlap = {"Type": "ANY", "CriteriaList": [{
        "Criteria": {"VisitOccurrence": {"CodesetId": 3}},
        "StartWindow": {"Start": {"Coeff": -1}, "End": {"Days": 0, "Coeff": 1}},
        "EndWindow": {"Start": {"Days": 0, "Coeff": -1}, "End": {"Coeff": 1}, "UseEventEnd": True},
        "Occurrence": {"Type": 2, "Count": 1},
    }]}  # fmt: skip
    periods = pl.LazyFrame({"person_id": [1, 2], "op_start": [d("2150-01-01")] * 2,
                            "op_end": [d("2150-01-20")] * 2})  # fmt: skip
    df = definition(AdditionalCriteria=overlap,
                    EndStrategy={"DateOffset": {"DateField": "EndDate", "Offset": 30}})  # fmt: skip
    # condition end is start + 1; + 30 days is past the observation period, so it ends there
    assert rows(cohort(df, data(pool, visits, periods), CODESETS)) == [
        (1, d("2150-01-02"), d("2150-01-20"))
    ]


def test_eras_merge_overlapping_periods_with_the_pad():
    periods = pl.DataFrame({
        "person_id": [1, 1, 1], "start_date": [d("2150-01-01"), d("2150-01-05"), d("2150-01-20")],
        "end_date": [d("2150-01-03"), d("2150-01-06"), d("2150-01-21")],
    })  # fmt: skip
    assert rows(_collapse(periods, 0)) == [
        (1, d("2150-01-01"), d("2150-01-03")),
        (1, d("2150-01-05"), d("2150-01-06")),
        (1, d("2150-01-20"), d("2150-01-21")),
    ]
    assert rows(_collapse(periods, 7)) == [
        (1, d("2150-01-01"), d("2150-01-06")),
        (1, d("2150-01-20"), d("2150-01-21")),
    ]


def test_unsupported_logic_is_refused():
    df = definition(CensoringCriteria=[criteria(1)])
    with pytest.raises(Unsupported):
        cohort(df, data(events()), CODESETS)


def test_the_qualified_limit_needs_additional_criteria_the_expression_limit_does_not():
    pool = events(
        {"person_id": 1, "concept_id": 100, "start_date": d("2150-01-01")},
        {"person_id": 1, "concept_id": 100, "start_date": d("2150-06-01")},
    )
    qualified = definition(QualifiedLimit={"Type": "First"})
    assert len(rows(cohort(qualified, data(pool), CODESETS))) == 2  # ignored, as in Circe
    expression = definition(ExpressionLimit={"Type": "First"})
    assert rows(cohort(expression, data(pool), CODESETS)) == [(1, d("2150-01-01"), d("2150-01-01"))]
