from datetime import date

import polars as pl
import pytest

from mimic_omop.cdm.schema import CdmValidationError, conform, schema, validate


def test_conform_orders_casts_and_adds_missing_columns():
    lf = pl.LazyFrame(
        {"death_date": [date(2150, 1, 1)], "person_id": [1]},
        schema_overrides={"person_id": pl.Int32},
    )
    df = conform(lf, "death").collect()
    assert df.schema == schema("death")
    assert df.columns[:2] == ["person_id", "death_date"]
    assert df["cause_concept_id"].to_list() == [None]


def test_conform_rejects_columns_not_in_spec():
    with pytest.raises(CdmValidationError, match="persn_id"):
        conform(pl.LazyFrame({"persn_id": [1]}), "death")


def test_validate_reports_nulls_in_required_fields_and_duplicate_keys():
    df = conform(
        pl.LazyFrame({"person_id": [1, 1], "death_date": [date(2150, 1, 1), None]}), "death"
    )
    assert validate(df, "death") == ["death.death_date: 1 null values in required field"]

    df = conform(
        pl.LazyFrame(
            {"person_id": [1, 1], "gender_concept_id": [0, 0], "year_of_birth": [1990, 1990],
             "race_concept_id": [0, 0], "ethnicity_concept_id": [0, 0]}
        ),
        "person",
    )  # fmt: skip
    assert validate(df, "person") == ["person.person_id: 1 duplicate primary key values"]


def test_targets_cdm_55():
    from mimic_omop.cdm.spec import CDM_VERSION, TABLES

    assert CDM_VERSION == "5.5"
    specimen = {f.name for f in TABLES["specimen"]}
    assert {"visit_occurrence_id", "visit_detail_id"} <= specimen  # added in 5.5
    assert {"pack_content", "concept_metadata", "concept_relationship_metadata"} <= set(TABLES)


def test_validate_treats_primary_keys_as_required():
    lf = pl.LazyFrame({"vocabulary_id": [None, "ICD10CM"]}, schema={"vocabulary_id": pl.String})
    assert validate(conform(lf, "vocabulary"), "vocabulary") == [
        "vocabulary.vocabulary_id: 1 null values in required field",
        "vocabulary.vocabulary_name: 2 null values in required field",
        "vocabulary.vocabulary_concept_id: 2 null values in required field",
    ]


def test_validate_checks_varchar_length():
    lf = conform(
        pl.LazyFrame(
            {"person_id": [1], "death_date": [date(2150, 1, 1)], "cause_source_value": ["x" * 51]}
        ),
        "death",
    )
    assert validate(lf, "death") == ["death.cause_source_value: 1 values longer than 50 characters"]
