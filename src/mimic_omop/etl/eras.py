"""condition_era and drug_era, following the standard OHDSI era logic.

- condition_era: per person and condition concept (concept 0 excluded), records whose start
  falls within 30 days of the end of the previous one are combined. A record without an end date
  counts as one day (start + 1 day).
- drug_era: drug concepts are rolled up to their RxNorm / RxNorm Extension ingredients through
  concept_ancestor (a combination drug counts for each ingredient). Overlapping exposures of an
  ingredient are first merged; merged exposures within 30 days of each other then form one era.
  gap_days is the era's length minus the days its exposures cover. Exposures of all types count
  (orders and administrations, see docs/review.md R17); drugs without an ingredient are left out.
- dose_era is not built: MIMIC drug records have no dose in OMOP form (review R20).

Era ids are person_id * 10**6 + the era's position for that person.
"""

from datetime import timedelta

import polars as pl

from mimic_omop.context import Inputs
from mimic_omop.etl.intervals import islands

PERSISTENCE_GAP = timedelta(days=30)
ERA_KEY = 10**6


def _merge(lf: pl.LazyFrame, by: list[str], gap: timedelta | None) -> pl.LazyFrame:
    """Merge (start, end) intervals per `by`: returns start, end and the rows merged (n)."""
    return (
        islands(
            lf.with_columns(order=pl.int_range(pl.len()).over(by, order_by=["start", "end"])),
            by=by,
            start="start",
            end="end",
            order_id="order",
            gap=gap,
        )
        .group_by(*by, "island")
        .agg(start=pl.col("start").min(), end=pl.col("end").max(), n=pl.col("n").sum())
        .drop("island")
    )


def _era_id(*order: str) -> pl.Expr:
    return pl.col("person_id") * ERA_KEY + pl.int_range(pl.len()).over(
        "person_id", order_by=list(order)
    )


def condition_era(conditions: pl.LazyFrame) -> pl.LazyFrame:
    records = conditions.filter(pl.col("condition_concept_id") != 0).select(
        "person_id",
        concept="condition_concept_id",
        start="condition_start_date",
        end=pl.coalesce("condition_end_date", pl.col("condition_start_date") + pl.duration(days=1)),
        n=pl.lit(1, pl.Int64),
    )
    eras = _merge(records, ["person_id", "concept"], PERSISTENCE_GAP)
    return eras.select(
        condition_era_id=_era_id("concept", "start"),
        person_id="person_id",
        condition_concept_id="concept",
        condition_era_start_date="start",
        condition_era_end_date="end",
        condition_occurrence_count="n",
    )


def ingredients(concept: pl.LazyFrame, concept_ancestor: pl.LazyFrame) -> pl.LazyFrame:
    """drug_concept_id -> ingredient concept_id, for RxNorm / RxNorm Extension ingredients."""
    ingredient = concept.filter(
        pl.col("concept_class_id") == "Ingredient",
        pl.col("vocabulary_id").is_in(["RxNorm", "RxNorm Extension"]),
        pl.col("standard_concept") == "S",
        pl.col("invalid_reason").is_null(),
    ).select(ancestor_concept_id="concept_id")
    return concept_ancestor.join(ingredient, on="ancestor_concept_id", how="semi").select(
        drug_concept_id="descendant_concept_id", ingredient="ancestor_concept_id"
    )


def drug_era(drugs: pl.LazyFrame, drug_ingredients: pl.LazyFrame) -> pl.LazyFrame:
    exposures = (
        drugs.filter(pl.col("drug_concept_id") != 0)
        .join(drug_ingredients, on="drug_concept_id")
        .select(
            "person_id",
            "ingredient",
            start="drug_exposure_start_date",
            end=pl.max_horizontal("drug_exposure_end_date", "drug_exposure_start_date"),
            n=pl.lit(1, pl.Int64),
        )
    )
    by = ["person_id", "ingredient"]
    merged = _merge(exposures, by, None).with_columns(
        covered=(pl.col("end") - pl.col("start")).dt.total_days()
    )
    eras = (
        islands(
            merged.with_columns(order=pl.int_range(pl.len()).over(by, order_by=["start", "end"])),
            by=by,
            start="start",
            end="end",
            order_id="order",
            gap=PERSISTENCE_GAP,
        )
        .group_by(*by, "island")
        .agg(
            start=pl.col("start").min(),
            end=pl.col("end").max(),
            n=pl.col("n").sum(),
            covered=pl.col("covered").sum(),
        )
    )
    return eras.select(
        drug_era_id=_era_id("ingredient", "start"),
        person_id="person_id",
        drug_concept_id="ingredient",
        drug_era_start_date="start",
        drug_era_end_date="end",
        drug_exposure_count="n",
        gap_days=(pl.col("end") - pl.col("start")).dt.total_days() - pl.col("covered"),
    )


def build_condition_era(inp: Inputs) -> pl.LazyFrame:
    return condition_era(inp.cdm.scan("condition_occurrence"))


def build_drug_era(inp: Inputs) -> pl.LazyFrame:
    drugs = inp.cdm.scan("drug_exposure")
    used = drugs.select(descendant_concept_id="drug_concept_id").unique()
    ancestor = inp.vocab.athena("concept_ancestor").join(
        used, on="descendant_concept_id", how="semi"
    )
    return drug_era(drugs, ingredients(inp.vocab.athena("concept"), ancestor))
