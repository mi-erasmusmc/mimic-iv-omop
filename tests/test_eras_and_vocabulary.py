from datetime import date, timedelta

import polars as pl

from mimic_omop.etl import eras, vocabulary

D0 = date(2150, 1, 1)


def day(n: int) -> date:
    return D0 + timedelta(days=n)


def test_condition_records_within_30_days_form_one_era():
    conditions = pl.LazyFrame(
        [
            (1, 100, day(0), None),  # counts as one day
            (1, 100, day(31), day(40)),  # starts 30 days after the first ended: same era
            (1, 100, day(71), None),  # 31 days after: a new era
            (1, 200, day(0), None),
            (1, 0, day(0), None),  # unmapped: no era
        ],
        schema={"person_id": pl.Int64, "condition_concept_id": pl.Int64,
                "condition_start_date": pl.Date, "condition_end_date": pl.Date},
        orient="row",
    )  # fmt: skip
    df = eras.condition_era(conditions).collect().sort("condition_era_id")
    assert df.select(
        "condition_concept_id", "condition_era_start_date", "condition_era_end_date", "condition_occurrence_count"
    ).rows() == [
        (100, day(0), day(40), 2),
        (100, day(71), day(72), 1),
        (200, day(0), day(1), 1),
    ]  # fmt: skip
    assert df["condition_era_id"].to_list() == [1_000_000, 1_000_001, 1_000_002]


def test_drug_eras_by_ingredient_with_gap_days():
    drugs = pl.LazyFrame(
        [
            (1, 10, day(0), day(10)),  # combination drug: ingredients 1 and 2
            (1, 11, day(5), day(15)),  # overlaps, ingredient 1
            (1, 11, day(25), day(30)),  # 10-day gap: same era
            (1, 99, day(0), day(1)),  # no ingredient: no era
        ],
        schema={"person_id": pl.Int64, "drug_concept_id": pl.Int64,
                "drug_exposure_start_date": pl.Date, "drug_exposure_end_date": pl.Date},
        orient="row",
    )  # fmt: skip
    ingredient_of = pl.LazyFrame({"drug_concept_id": [10, 10, 11], "ingredient": [1, 2, 1]})
    df = eras.drug_era(drugs, ingredient_of).collect().sort("drug_concept_id")
    assert df.select(
        "drug_concept_id", "drug_era_start_date", "drug_era_end_date", "drug_exposure_count", "gap_days"
    ).rows() == [
        (1, day(0), day(30), 3, 10),  # 30 days long, 20 covered (0-15, 25-30)
        (2, day(0), day(10), 1, 0),
    ]  # fmt: skip


def concept_rows(*rows: tuple[int, str, str | None]) -> pl.LazyFrame:
    """(concept_id, domain_id, standard_concept), as an Athena-typed concept table."""
    return pl.LazyFrame(
        [{"concept_id": i, "concept_name": f"c{i}", "domain_id": d, "vocabulary_id": "X",
          "concept_class_id": "X", "standard_concept": s, "concept_code": str(i),
          "valid_start_date": D0, "valid_end_date": D0, "invalid_reason": None} for i, d, s in rows],
        schema_overrides={"standard_concept": pl.String, "invalid_reason": pl.String},
    )  # fmt: skip


def as_text(lf: pl.LazyFrame) -> pl.LazyFrame:
    return lf.select(pl.all().cast(pl.String))


def test_cvb_concepts_and_resolved_mappings_join_the_output_vocabulary():
    athena = concept_rows((1, "Condition", "S"), (2, "Condition", None))
    cvb_concepts = as_text(concept_rows((2_061_000_001, "Observation", None), (2_061_000_002, "Undefined", "S"), (2_061_000_003, "Observation", "S"), (1, "X", None)))  # fmt: skip
    # CVB said 'Maps to' 2, which is not standard; the ETL resolved it to 1. 2061000003 is a
    # standard MIMIC4 concept the ETL maps to its broader concept 1 (review R29)
    resolved = pl.LazyFrame(
        {
            "source_concept_id": ["2061000001", "2061000003"],
            "relationship_id": ["Maps to", "Maps to"],
            "target_concept_id": ["1", "1"],
        }
    )
    concepts = vocabulary.concept(athena, cvb_concepts, resolved)
    assert sorted(concepts.select("concept_id", "standard_concept").collect().rows()) == [
        (1, "S"),
        (2, None),
        (2_061_000_001, None),
        (2_061_000_002, "S"),
        (2_061_000_003, None),  # mapped uphill: no longer standard
    ]  # Athena wins

    cvb_relationships = pl.LazyFrame(
        {"concept_id_1": ["2061000001", "2061000001", "2061000001"], "concept_id_2": ["2", "1", "999"],
         "relationship_id": ["Maps to", "Is a", "Is a"], "valid_start_date": ["2025-01-01"] * 3,
         "valid_end_date": ["2099-12-31"] * 3, "invalid_reason": [None] * 3},
        schema_overrides={"invalid_reason": pl.String},
    )  # fmt: skip
    athena_rel = pl.LazyFrame(
        schema={"concept_id_1": pl.Int64, "concept_id_2": pl.Int64, "relationship_id": pl.String,
                "valid_start_date": pl.Date, "valid_end_date": pl.Date, "invalid_reason": pl.String}
    )  # fmt: skip
    rel = vocabulary.concept_relationship(
        athena_rel, cvb_relationships, cvb_concepts, resolved, concepts
    )
    assert sorted(
        rel.select("concept_id_1", "concept_id_2", "relationship_id").collect().rows()
    ) == [
        (1, 2_061_000_001, "Mapped from"),
        (1, 2_061_000_003, "Mapped from"),
        (2_061_000_001, 1, "Is a"),  # kept; the 'Is a' to 999 (not a concept) is dropped
        (2_061_000_001, 1, "Maps to"),  # the resolved mapping, not CVB's
        (2_061_000_002, 2_061_000_002, "Mapped from"),
        (2_061_000_002, 2_061_000_002, "Maps to"),  # standard custom concept maps to itself
        (2_061_000_003, 1, "Maps to"),  # and not to itself
    ]

    domains = pl.LazyFrame(
        {"domain_id": ["Observation"], "domain_name": ["Observation"], "domain_concept_id": [27]}
    )
    out = vocabulary.domain(domains, cvb_concepts).collect().sort("domain_id")
    assert out.rows() == [
        ("Observation", "Observation", 27),
        ("Undefined", "Undefined", 0),
        ("X", "X", 0),
    ]
