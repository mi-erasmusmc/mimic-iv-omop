"""Vocabulary tables of the output CDM: the Athena vocabularies, plus CVB's MIMIC4 vocabulary.

CVB's MIMIC4 concepts (ids from 2,000,000,000) are the *_source_concept_id of MIMIC's local codes,
so they are added to concept, with their vocabulary and concept classes. Their relationships come
from CVB, except the mappings ('Maps to', 'Maps to value' and their reverses), which are written
as the ETL resolved them through Athena (see vocab.resolve_cvb), so the vocabulary says what the
data does. CVB's standard MIMIC4 concepts keep their 'Maps to' self, unless the ETL maps them
to a broader Athena concept (review R29): those are written as non-standard. Relationships whose
concepts are not in concept are left out. Athena's 'None' vocabulary row is kept; the CVB version is
recorded in METADATA. Domains CVB's concepts use but do not define are added with
domain_concept_id 0.
"""

import polars as pl

from mimic_omop.cdm.schema import dtype
from mimic_omop.cdm.spec import TABLES
from mimic_omop.context import Inputs

MAPPING_RELATIONSHIPS = {
    "Maps to": "Mapped from",
    "Maps to value": "Value mapped from",
}
OPEN_START, OPEN_END = "1970-01-01", "2099-12-31"


def typed(lf: pl.LazyFrame, table: str) -> pl.LazyFrame:
    """Cast a CVB file's text columns to the CDM types of `table` (dates are YYYY-MM-DD)."""
    exprs = []
    for f in TABLES[table]:
        target = dtype(f)
        col = pl.col(f.name)
        exprs.append(
            (col.str.to_date("%Y-%m-%d") if target == pl.Date() else col.cast(target)).alias(f.name)
        )
    return lf.select(exprs)


def mapped_sources(resolved_mappings: pl.LazyFrame) -> pl.LazyFrame:
    """concept_id of the MIMIC4 concepts the ETL maps to a standard concept."""
    return (
        resolved_mappings.filter(
            pl.col("relationship_id") == "Maps to",
            pl.col("target_concept_id").cast(pl.Int64, strict=False).fill_null(0) != 0,
        )
        .select(concept_id=pl.col("source_concept_id").cast(pl.Int64, strict=False))
        .drop_nulls()
        .unique()
    )


def concept(
    athena: pl.LazyFrame, cvb: pl.LazyFrame, resolved_mappings: pl.LazyFrame | None = None
) -> pl.LazyFrame:
    """Athena's concepts and CVB's; a MIMIC4 concept CVB marks standard but the ETL maps to
    another concept is not standard."""
    cvb_concepts = typed(cvb, "concept").join(athena, on="concept_id", how="anti")
    if resolved_mappings is not None:
        mapped = mapped_sources(resolved_mappings).with_columns(mapped=pl.lit(True))
        cvb_concepts = (
            cvb_concepts.join(mapped, on="concept_id", how="left")
            .with_columns(
                standard_concept=pl.when(pl.col("mapped")).then(None).otherwise("standard_concept")
            )
            .drop("mapped")
        )
    return pl.concat([athena, cvb_concepts])


def concept_relationship(
    athena: pl.LazyFrame,
    cvb_relationships: pl.LazyFrame,
    cvb_concepts: pl.LazyFrame,
    resolved_mappings: pl.LazyFrame,
    concepts: pl.LazyFrame,
) -> pl.LazyFrame:
    """resolved_mappings: CVB curation rows after resolve_cvb (source_concept_id,
    relationship_id, target_concept_id as text)."""
    other = typed(cvb_relationships, "concept_relationship").filter(
        ~pl.col("relationship_id").is_in([*MAPPING_RELATIONSHIPS, *MAPPING_RELATIONSHIPS.values()])
    )
    forward = (
        resolved_mappings.filter(pl.col("relationship_id").is_in(list(MAPPING_RELATIONSHIPS)))
        .select(
            concept_id_1=pl.col("source_concept_id").cast(pl.Int64),
            concept_id_2=pl.col("target_concept_id").cast(pl.Int64),
            relationship_id="relationship_id",
        )
        .filter(pl.col("concept_id_2") != 0)
    )
    standard_self = (
        typed(cvb_concepts, "concept")
        .filter(pl.col("standard_concept") == "S")
        .join(mapped_sources(resolved_mappings), on="concept_id", how="anti")
        .select(
            concept_id_1="concept_id", concept_id_2="concept_id", relationship_id=pl.lit("Maps to")
        )
    )
    mappings = pl.concat([forward, standard_self])
    reverse = mappings.select(
        concept_id_1="concept_id_2",
        concept_id_2="concept_id_1",
        relationship_id=pl.col("relationship_id").replace_strict(MAPPING_RELATIONSHIPS),
    )
    generated = pl.concat([mappings, reverse]).with_columns(
        valid_start_date=pl.lit(OPEN_START).str.to_date(),
        valid_end_date=pl.lit(OPEN_END).str.to_date(),
        invalid_reason=pl.lit(None, pl.String),
    )
    ids = concepts.select("concept_id")
    cvb = (
        pl.concat([other, generated], how="diagonal_relaxed")
        .unique(["concept_id_1", "concept_id_2", "relationship_id"])
        .join(ids.rename({"concept_id": "concept_id_1"}), on="concept_id_1", how="semi")
        .join(ids.rename({"concept_id": "concept_id_2"}), on="concept_id_2", how="semi")
    )
    return pl.concat([athena, cvb.select(athena.collect_schema().names())], how="vertical_relaxed")


def vocabulary(athena: pl.LazyFrame, cvb: pl.LazyFrame) -> pl.LazyFrame:
    return pl.concat(
        [athena, typed(cvb, "vocabulary").join(athena, on="vocabulary_id", how="anti")]
    )


def concept_class(athena: pl.LazyFrame, cvb: pl.LazyFrame) -> pl.LazyFrame:
    return pl.concat(
        [athena, typed(cvb, "concept_class").join(athena, on="concept_class_id", how="anti")]
    )


def domain(athena: pl.LazyFrame, cvb_concepts: pl.LazyFrame) -> pl.LazyFrame:
    """Athena's domains, plus the domains CVB's concepts use without defining them ('Undefined',
    'Waveform Metadata'), with domain_concept_id 0."""
    used = typed(cvb_concepts, "concept").select("domain_id").unique()
    missing = used.join(athena, on="domain_id", how="anti").select(
        "domain_id", domain_name="domain_id", domain_concept_id=pl.lit(0, pl.Int64)
    )
    return pl.concat([athena, missing.select(athena.collect_schema().names())])


def build_concept(inp: Inputs) -> pl.LazyFrame:
    return concept(
        inp.vocab.athena("concept"), inp.vocab.cvb_ontology("concept_delta"), inp.vocab.cvb()
    )


def build_concept_relationship(inp: Inputs) -> pl.LazyFrame:
    return concept_relationship(
        inp.vocab.athena("concept_relationship"),
        inp.vocab.cvb_ontology("concept_relationship_delta"),
        inp.vocab.cvb_ontology("concept_delta"),
        inp.vocab.cvb(),
        build_concept(inp),
    )


def build_vocabulary(inp: Inputs) -> pl.LazyFrame:
    return vocabulary(inp.vocab.athena("vocabulary"), inp.vocab.cvb_ontology("vocabulary_delta"))


def build_concept_class(inp: Inputs) -> pl.LazyFrame:
    return concept_class(
        inp.vocab.athena("concept_class"), inp.vocab.cvb_ontology("concept_class_delta")
    )


def build_domain(inp: Inputs) -> pl.LazyFrame:
    return domain(inp.vocab.athena("domain"), inp.vocab.cvb_ontology("concept_delta"))


def athena_table(table: str):
    """A vocabulary table copied from Athena as it is."""

    def build(inp: Inputs) -> pl.LazyFrame:
        return inp.vocab.athena(table)

    return build


BUILDERS = {
    "concept": build_concept,
    "concept_relationship": build_concept_relationship,
    "vocabulary": build_vocabulary,
    "concept_class": build_concept_class,
    "domain": build_domain,
    **{
        t: athena_table(t)
        for t in (
            "relationship",
            "concept_ancestor",
            "concept_synonym",
            "drug_strength",
            "concept_metadata",
            "concept_relationship_metadata",
            "pack_content",
        )
    },
}
