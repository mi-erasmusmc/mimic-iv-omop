from pathlib import Path

import polars as pl
import pytest
from conftest import cvb

from mimic_omop.vocab import code_map, load_athena

ICD = ("ICD9CM", "ICD10CM")
SET = "mimiciv_hosp.d_icd_diagnoses.icd_code"


def concepts(*rows: tuple) -> pl.LazyFrame:
    """(concept_id, vocabulary_id, concept_code, domain_id, standard_concept[, concept_class_id])."""
    return pl.LazyFrame(
        [
            {"concept_id": i, "vocabulary_id": v, "concept_code": code, "domain_id": d,
             "standard_concept": s, "concept_class_id": (cls or ["X"])[0], "invalid_reason": None}
            for i, v, code, d, s, *cls in rows
        ],
        schema_overrides={"invalid_reason": pl.String},
    )  # fmt: skip


def relationships(*rows: tuple[int, int, str]) -> pl.LazyFrame:
    return pl.LazyFrame(
        [{"concept_id_1": a, "concept_id_2": b, "relationship_id": r, "invalid_reason": None} for a, b, r in rows],
        schema={"concept_id_1": pl.Int64, "concept_id_2": pl.Int64, "relationship_id": pl.String, "invalid_reason": pl.String},
    )  # fmt: skip


CONCEPT = concepts(
    (1, "ICD10CM", "E11.9", "Condition", None),
    (2, "ICD10CM", "Z86.73", "Observation", None),
    (3, "ICD10CM", "R99", "Condition", None),  # no mapping in Athena
    (4, "ICD9CM", "V12", "Observation", None),  # same dotless code exists in both versions
    (5, "ICD10CM", "V12", "Observation", None),
    (100, "SNOMED", "44054006", "Condition", "S"),
    (101, "OMOP", "history", "Observation", "S"),
    (102, "SNOMED", "TIA", "Condition", "S"),
    (103, "SNOMED", "dead", "Condition", None),  # not standard: not a valid target
)
RELATIONSHIP = relationships(
    (1, 100, "Maps to"),
    (1, 103, "Maps to"),
    (2, 101, "Maps to"),
    (2, 102, "Maps to value"),
    (4, 101, "Maps to"),
)


def rows(lf: pl.LazyFrame) -> list[tuple]:
    return (
        lf.select("vocabulary_id", "source_code", "source_concept_id", "target_concept_id", "value_as_concept_id")
        .sort(pl.all())
        .collect()
        .rows()
    )  # fmt: skip


def test_athena_maps_to_standard_targets_with_values():
    assert rows(code_map(CONCEPT, RELATIONSHIP, cvb(), ICD, strip_dots=True)) == [
        ("ICD10CM", "E119", 1, 100, None),
        ("ICD10CM", "R99", 3, None, None),
        ("ICD10CM", "V12", 5, None, None),
        ("ICD10CM", "Z8673", 2, 101, 102),
        ("ICD9CM", "V12", 4, 101, None),
    ]


def test_cvb_fills_only_codes_athena_does_not_map():
    mapping = cvb(
        (SET, "E119", "Maps to", 999, "Condition"),  # Athena maps it: ignored
        (SET, "R99", "Maps to", 200, "Condition"),  # Athena has the code unmapped: filled
        (SET, "V12", "Maps to", 201, "Observation"),  # only fills ICD10CM, where Athena has no map
        (SET, "X1234", "Maps to", 202, "Condition"),  # not in Athena at all: applies to both
    )
    out = rows(code_map(CONCEPT, RELATIONSHIP, mapping, ICD, SET, strip_dots=True))
    assert ("ICD10CM", "E119", 1, 100, None) in out and not [r for r in out if r[3] == 999]
    assert ("ICD10CM", "R99", 3, 200, None) in out
    assert ("ICD10CM", "V12", 5, 201, None) in out and ("ICD9CM", "V12", 4, 101, None) in out
    assert {r[0] for r in out if r[1] == "X1234"} == {"ICD9CM", "ICD10CM"}
    assert not [r for r in out if r[1] == "R99" and r[3] is None]  # no leftover unmapped row


def test_load_athena_types_tab_files_without_quoting(tmp_path):
    (tmp_path / "CONCEPT.csv").write_text(
        "concept_id\tconcept_name\tdomain_id\tvocabulary_id\tconcept_class_id\tstandard_concept\t"
        "concept_code\tvalid_start_date\tvalid_end_date\tinvalid_reason\n"
        '201826\tType 2 "diabetes"\tCondition\tSNOMED\tDisorder\tS\t44054006\t20020131\t20991231\t\n'
    )
    (tmp_path / "README.txt").write_text("not a table")
    assert load_athena(tmp_path, tmp_path / "vocab") == {"concept": 1}
    row = pl.read_parquet(tmp_path / "vocab/athena/concept.parquet").row(0, named=True)
    assert row["concept_id"] == 201826 and row["concept_name"] == 'Type 2 "diabetes"'
    assert str(row["valid_start_date"]) == "2002-01-31" and row["invalid_reason"] is None


def test_missing_vocabularies_are_reported():
    from mimic_omop.vocab import NEEDED_VOCABULARIES, missing_vocabularies

    present = pl.LazyFrame({"vocabulary_id": [v for v in NEEDED_VOCABULARIES if v != "UCUM"]})
    assert missing_vocabularies(present) == ["UCUM"]


def test_cvb_targets_are_resolved_to_valid_standard_concepts():
    from mimic_omop.vocab import resolve_cvb

    concept = concepts(
        (100, "SNOMED", "a", "Condition", "S"),
        (101, "SNOMED", "b", "Condition", None),  # de-standardised, maps on to 102
        (102, "SNOMED", "c", "Observation", "S"),
        (103, "SNOMED", "d", "Condition", None),  # de-standardised, no replacement
    )
    relationship = relationships((101, 102, "Maps to"))
    mapping = cvb(
        (SET, "A", "Maps to", 100, "Measurement"),  # stale domain is corrected
        (SET, "B", "Maps to", 101, "Condition"),
        (SET, "D", "Maps to", 103, "Condition"),
        (SET, "M", "Maps to", 2_062_495_454, "Visit"),  # CVB custom concept, not in Athena
        (SET, "U", None, 0, None),  # listed without a mapping: kept as is
    )
    out = resolve_cvb(mapping, concept, relationship, ancestors()).collect()
    got = dict(
        zip(
            out["source_code"],
            zip(out["target_concept_id"], out["target_domain_id"], strict=True),
            strict=True,
        )
    )
    assert got == {
        "A": ("100", "Condition"),
        "B": ("102", "Observation"),
        "D": ("0", None),
        "M": ("0", None),
        "U": ("0", None),
    }


def ancestors(*pairs: tuple[int, int]) -> pl.LazyFrame:
    return pl.LazyFrame(
        [{"ancestor_concept_id": a, "descendant_concept_id": d, "min_levels_of_separation": 1} for a, d in pairs],
        schema={"ancestor_concept_id": pl.Int64, "descendant_concept_id": pl.Int64, "min_levels_of_separation": pl.Int64},
    )  # fmt: skip


def test_codes_without_maps_to_are_mapped_to_their_broader_concept():
    from mimic_omop.vocab import resolve_cvb

    concept = concepts(
        (10, "RxNorm", "midazolam", "Drug", "S", "Ingredient"),
        (11, "RxNorm", "bupivacaine", "Drug", "S", "Ingredient"),
        (20, "LOINC", "panel", "Measurement", "S"),
        (21, "LOINC", "sbp sitting", "Measurement", "S"),
        (30, "SNOMED", "motor", "Measurement", "S"),
        (31, "SNOMED", "arm", "Measurement", "S"),
        (40, "SNOMED", "heart rate", "Measurement", "S"),
    )
    mapping = cvb(
        (SET, "MIDAZ", "Is a", 10, "Drug"),
        (SET, "MIX", "Is a", 10, "Drug"),  # combination: one target per ingredient
        (SET, "MIX", "Is a", 11, "Drug"),
        (SET, "ORTHO", "Is a", 20, "Measurement"),  # 20 is an ancestor of 21: 21 is used
        (SET, "ORTHO", "Is a", 21, "Measurement"),
        (SET, "MOTOR", "Is a", 30, "Measurement"),  # two unrelated broader concepts: unmapped
        (SET, "MOTOR", "Is a", 31, "Measurement"),
        (SET, "HR", "Maps to", 40, "Measurement"),  # a 'Maps to' wins over 'Is a'
        (SET, "HR", "Is a", 30, "Measurement"),
        (SET, "OLD", "Maps to", 999, "Measurement"),  # 'Maps to' gives no target: 'Is a' is used
        (SET, "OLD", "Is a", 40, "Measurement"),
    )
    out = resolve_cvb(mapping, concept, relationships(), ancestors((20, 21))).collect()
    maps_to = (
        out.filter(pl.col("relationship_id") == "Maps to", pl.col("target_concept_id") != "0")
        .select("source_code", "target_concept_id", "target_domain_id")
        .sort(pl.all())
        .rows()
    )
    assert maps_to == [
        ("HR", "40", "Measurement"),
        ("MIDAZ", "10", "Drug"),
        ("MIX", "10", "Drug"),
        ("MIX", "11", "Drug"),
        ("OLD", "40", "Measurement"),
        ("ORTHO", "21", "Measurement"),
    ]
    assert out.filter(pl.col("relationship_id") == "Is a").height == 9  # kept as they are


CVB_FILE = Path(__file__).parents[1] / "data" / "vocab" / "cvb_mimic_mapping.parquet"


@pytest.mark.skipif(not CVB_FILE.exists(), reason="needs the CVB mapping (fetch-cvb)")
def test_every_cvb_exclusion_matches_one_cvb_relationship():
    """An exclusion that matches nothing is stale (CVB corrected it, or the key is mistyped)."""
    from mimic_omop.vocab import EXCLUSION_KEY, cvb_exclusions

    cvb = pl.read_parquet(CVB_FILE).select(EXCLUSION_KEY)
    excluded = cvb_exclusions().collect()
    matched = excluded.join(cvb, on=EXCLUSION_KEY, how="semi")
    assert matched.height == excluded.height, excluded.join(cvb, on=EXCLUSION_KEY, how="anti")
