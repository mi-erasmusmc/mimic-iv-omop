"""Charlson comorbidity index from standard concepts (evaluation E2a, variant b).

mimic-code defines each Charlson component by ICD code prefixes. To ask whether the CDM's
standard concepts keep that meaning, the components are translated to concept sets the way an
OMOP analyst would build them, without reimplementing mimic-code's prefix lists:

1. mimic-code's charlson.sql runs on every ICD-9-CM and ICD-10-CM code in Athena, each as its
   own admission, which gives each code's components (`code_components`);
2. a component's concept set is the standard concepts its codes map to ('Maps to'), with the
   'Maps to value' concept where the code has one (a "history of" code maps to History of
   event with the condition as its value, and only the pair has the code's meaning), and
   optionally their descendants (`concept_sets`);
3. on the CDM, an admission has a component if any of its records has a concept, and value
   where the set has one, in the set;
   each (admission, component) is written as one representative ICD code of that component
   only, and the same charlson.sql then computes the score (`diagnoses_by_concept`).
"""

from pathlib import Path

import polars as pl

from mimic_eval import cdm_tables, mimic_code

COMPONENTS = [
    "myocardial_infarct",
    "congestive_heart_failure",
    "peripheral_vascular_disease",
    "cerebrovascular_disease",
    "dementia",
    "chronic_pulmonary_disease",
    "rheumatic_disease",
    "peptic_ulcer_disease",
    "mild_liver_disease",
    "diabetes_without_cc",
    "diabetes_with_cc",
    "paraplegia",
    "renal_disease",
    "malignant_cancer",
    "severe_liver_disease",
    "metastatic_solid_tumor",
    "aids",
]


def icd_codes(vocab: Path) -> pl.DataFrame:
    """Every ICD-9-CM and ICD-10-CM code in Athena, dotless as in MIMIC."""
    return (
        pl.scan_parquet(Path(vocab) / "athena/concept.parquet")
        .filter(pl.col("vocabulary_id").is_in(list(cdm_tables.ICD_VOCABULARIES)))
        .select(
            source_concept_id="concept_id",
            icd_code=pl.col("concept_code").str.replace_all(".", "", literal=True),
            icd_version=pl.col("vocabulary_id")
            .replace_strict(cdm_tables.ICD_VOCABULARIES)
            .cast(pl.Int64),
        )
        .collect()
        .with_row_index("hadm_id", offset=1)
        .with_columns(pl.col("hadm_id").cast(pl.Int64))
    )


def code_components(vocab: Path, mimic_code_dir: Path, work: Path) -> pl.DataFrame:
    """(source_concept_id, icd_code, icd_version, component) for every code mimic-code counts."""
    codes = icd_codes(vocab)
    one = codes.select("hadm_id", subject_id="hadm_id")
    tables = {
        "mimiciv_hosp.diagnoses_icd": codes.lazy().select(
            "hadm_id", "icd_code", "icd_version", subject_id="hadm_id"
        ),
        "mimiciv_hosp.admissions": one.lazy().with_columns(
            admittime=pl.datetime(2000, 1, 1), dischtime=pl.datetime(2000, 1, 2)
        ),
        "mimiciv_hosp.patients": one.lazy().select(
            "subject_id", anchor_age=pl.lit(60, pl.Int64), anchor_year=pl.lit(2000, pl.Int64)
        ),
    }
    out = mimic_code.run(tables, mimic_code.sql_files(mimic_code_dir, mimic_code.CHARLSON), work)
    flags = pl.read_parquet(out["charlson"]).unpivot(
        index="hadm_id", on=COMPONENTS, variable_name="component"
    )
    return (
        flags.filter(pl.col("value") == 1)
        .join(codes, on="hadm_id")
        .select("source_concept_id", "icd_code", "icd_version", "component")
    )


SET_KINDS = ("mapped", "specific", "descendants")


def concept_sets(components: pl.DataFrame, vocab: Path, kind: str) -> pl.DataFrame:
    """(component, concept_id, value_concept_id): value_concept_id is null for codes without a
    'Maps to value'. `kind`:

    - mapped: the standard concepts, and values, the component's codes map to;
    - specific: of those, the ones that only codes of the component map to. A code mapped to
      several concepts (290.21, dementia with depressive features, to Dementia and Depressive
      disorder) puts all of them in the mapped set, though each alone means less than the code;
    - descendants: the mapped set and every descendant of its concepts and values, as concept
      sets are usually built in ATLAS.
    """
    if kind not in SET_KINDS:
        raise ValueError(f"unknown concept set kind {kind!r}")
    athena = Path(vocab) / "athena"
    standard = pl.scan_parquet(athena / "concept.parquet").filter(
        pl.col("standard_concept") == "S", pl.col("invalid_reason").is_null()
    )

    def targets(relationship: str, name: str) -> pl.LazyFrame:
        return (
            pl.scan_parquet(athena / "concept_relationship.parquet")
            .filter(pl.col("relationship_id") == relationship, pl.col("invalid_reason").is_null())
            .join(standard.select(concept_id_2="concept_id"), on="concept_id_2", how="semi")
            .select(source_concept_id="concept_id_1", **{name: "concept_id_2"})
        )

    def mapped(codes: pl.LazyFrame) -> pl.LazyFrame:
        return codes.join(targets("Maps to", "concept_id"), on="source_concept_id").join(
            targets("Maps to value", "value_concept_id"), on="source_concept_id", how="left"
        )

    pair = ["concept_id", "value_concept_id"]
    sets = mapped(components.lazy()).select("component", *pair)
    if kind == "specific":
        all_codes = mapped(icd_codes(vocab).lazy().select("source_concept_id"))
        by_pair = all_codes.group_by(pair).agg(codes=pl.col("source_concept_id").n_unique())
        in_component = (
            mapped(components.lazy())
            .group_by("component", *pair)
            .agg(codes=pl.col("source_concept_id").n_unique())
        )
        sets = in_component.join(by_pair, on=[*pair, "codes"], nulls_equal=True).select(
            "component", *pair
        )
    elif kind == "descendants":
        below = pl.scan_parquet(athena / "concept_ancestor.parquet").select(
            "ancestor_concept_id", "descendant_concept_id"
        )
        sets = (
            sets.join(below, left_on="concept_id", right_on="ancestor_concept_id")
            .with_columns(concept_id="descendant_concept_id")
            .drop("descendant_concept_id")
            .join(below, left_on="value_concept_id", right_on="ancestor_concept_id", how="left")
            .with_columns(value_concept_id="descendant_concept_id")
            .select("component", *pair)
        )
    return sets.unique().collect()


def representatives(components: pl.DataFrame) -> pl.DataFrame:
    """One ICD code per component that counts for that component only."""
    only = components.filter(pl.len().over("source_concept_id") == 1)
    rep = only.sort("icd_version", "icd_code").group_by("component").first()
    missing = set(COMPONENTS) - set(rep["component"])
    if missing:
        raise ValueError(f"no code counts for these components only: {sorted(missing)}")
    return rep.select("component", "icd_code", "icd_version")


def diagnoses_by_concept(cdm: Path, sets: pl.DataFrame, reps: pl.DataFrame) -> pl.LazyFrame:
    """diagnoses_icd for the CDM by standard concept: one representative code per admission
    and component found."""

    def records(table: str, visit: str, concept: str) -> pl.LazyFrame:
        lf = cdm_tables.scan(cdm, table)
        value = "value_as_concept_id" if "value_as_concept_id" in lf.collect_schema() else None
        return lf.select(
            subject_id="person_id",
            hadm_id=visit,
            concept_id=concept,
            value_concept_id=pl.col(value) if value else pl.lit(None, pl.Int64),
        )

    parts = [records(t, v, c) for t, (v, _, _, c) in cdm_tables.EVENT_TABLES.items()]
    pairs = sets.filter(pl.col("value_concept_id").is_not_null()).lazy()
    single = sets.filter(pl.col("value_concept_id").is_null()).lazy().drop("value_concept_id")
    found = pl.concat(parts).filter(pl.col("hadm_id").is_not_null())
    return (
        pl.concat(
            [
                found.join(pairs, on=["concept_id", "value_concept_id"]),
                found.join(single, on="concept_id"),
            ],
            how="diagonal",
        )
        .select("subject_id", "hadm_id", "component")
        .unique()
        .join(reps.lazy(), on="component")
        .select(
            "subject_id",
            "hadm_id",
            seq_num=pl.lit(None, pl.Int64),
            icd_code="icd_code",
            icd_version="icd_version",
        )
    )
