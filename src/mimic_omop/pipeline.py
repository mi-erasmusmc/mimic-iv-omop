"""Run table builders, validate, and write CDM parquet.

Each table is streamed with `sink_parquet` into a staging directory (one part per person bucket),
validated with lazy queries over the staged parquet, and only then moved into place, so a failed
build never leaves partial output behind.

Output layout: <out>/<table>/part-<k>.parquet, readable as one table with e.g.
duckdb `read_parquet('<out>/person/*.parquet')` or polars `scan_parquet('<out>/person/')`.
"""

import logging
import resource
import shutil
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

import polars as pl

from mimic_omop.cdm.schema import CdmValidationError, conform, validate
from mimic_omop.cdm.spec import TABLES
from mimic_omop.context import Inputs, Release
from mimic_omop.etl import (
    care_site,
    cdm_source,
    condition_occurrence,
    death,
    device_exposure,
    drug_exposure,
    episode,
    eras,
    fact_relationship,
    measurement,
    observation,
    observation_period,
    payer_plan_period,
    person,
    procedure_occurrence,
    sources,
    specimen,
    visit_detail,
    visit_occurrence,
    vocabulary,
)

log = logging.getLogger(__name__)


@dataclass(frozen=True)
class Table:
    name: str
    build: Callable[[Inputs], pl.LazyFrame]
    per_person: bool = True  # built per person bucket; otherwise once from the whole source


# In dependency order: a builder may read any table listed before it through Inputs.cdm.
# observation_period needs every clinical event table, and person keeps only persons with an
# observation period, so both come after the event tables.
REGISTRY = (
    Table("care_site", care_site.build, per_person=False),
    Table("visit_occurrence", visit_occurrence.build),
    Table("visit_detail", visit_detail.build),
    Table("condition_occurrence", condition_occurrence.build),
    Table("procedure_occurrence", procedure_occurrence.build),
    Table("observation", observation.build),
    Table("measurement", measurement.build),
    Table("drug_exposure", drug_exposure.build),
    Table("device_exposure", device_exposure.build),
    Table("specimen", specimen.build),
    Table("fact_relationship", fact_relationship.build),
    Table("episode", episode.build),
    Table("episode_event", episode.build_event),
    Table("observation_period", observation_period.build),
    Table("person", person.build),
    Table("death", death.build),
    Table("payer_plan_period", payer_plan_period.build),
    Table("condition_era", eras.build_condition_era),
    Table("drug_era", eras.build_drug_era),
    *(Table(name, build, per_person=False) for name, build in vocabulary.BUILDERS.items()),
    Table("cdm_source", cdm_source.build_cdm_source, per_person=False),
    Table("metadata", cdm_source.build_metadata, per_person=False),
)
VOCABULARY_TABLES = set(vocabulary.BUILDERS)
EVENT_TABLES = {
    "condition_occurrence",
    "procedure_occurrence",
    "observation",
    "measurement",
    "drug_exposure",
    "device_exposure",
    "episode",
}
BUILDERS = {t.name: t for t in REGISTRY}


def run(
    source_dir: Path,
    vocab_dir: Path,
    out_dir: Path,
    tables: list[str] | None = None,
    buckets: int = 1,
    release: Release | None = None,
) -> dict[str, int]:
    unknown = set(tables or ()) - set(BUILDERS)
    if unknown:
        raise ValueError(f"no builder for {sorted(unknown)}; available: {list(BUILDERS)}")
    if release and release.observation_periods not in observation_period.STRATEGIES:
        raise ValueError(
            f"unknown observation period strategy {release.observation_periods!r}; "
            f"available: {list(observation_period.STRATEGIES)}"
        )
    selected = [t for t in REGISTRY if tables is None or t.name in tables]
    out_dir = Path(out_dir)
    inputs = Inputs.create(source_dir, vocab_dir, out_dir, release)

    counts = {}
    materialized = False
    for table in selected:
        if table.name in EVENT_TABLES and not materialized:
            shutil.rmtree(out_dir / sources.EVENTS_DIR, ignore_errors=True)
            for k in range(buckets):
                sources.materialize(inputs.for_bucket(k, buckets))
            log.info("events materialized (%s)", _memory())
            materialized = True
        staging = out_dir / ".staging" / table.name
        shutil.rmtree(staging, ignore_errors=True)
        staging.mkdir(parents=True)
        n = buckets if table.per_person else 1
        for k in range(n):
            inp = inputs.for_bucket(k, n) if table.per_person else inputs
            conform(table.build(inp), table.name).sink_parquet(staging / f"part-{k:04d}.parquet")

        staged = pl.scan_parquet(staging / "part-*.parquet")
        # Per person bucket: ids derive from person-specific source keys (subject, admission,
        # stay, source row), so buckets cannot share an id, and checking each part keeps the
        # uniqueness check of hundreds of millions of ids within memory.
        problems = [
            f"{part.name}: {problem}"
            for part in sorted(staging.glob("part-*.parquet"))
            for problem in validate(pl.scan_parquet(part), table.name)
        ]
        if problems:
            raise CdmValidationError("\n".join(problems))
        counts[table.name] = staged.select(pl.len()).collect().item()
        _replace_dir(staging, out_dir / table.name)
        log.info("%s: %d rows (%s)", table.name, counts[table.name], _memory())

    shutil.rmtree(out_dir / ".staging", ignore_errors=True)
    if problems := check_foreign_keys(out_dir):
        raise CdmValidationError("\n".join(problems))
    if inputs.vocab.has_athena():
        # the output vocabulary, once built, also holds CVB's MIMIC4 concepts
        output_concept = out_dir / "concept"
        concept = (
            pl.scan_parquet(output_concept / "part-*.parquet")
            if any(output_concept.glob("part-*.parquet"))
            else inputs.vocab.athena("concept")
        )
        problems, custom = check_concepts(out_dir, concept)
        if problems:
            raise CdmValidationError("\n".join(problems))
        for field, n in custom.items():
            log.warning("%s: %d custom concept ids (>= 2e9) not in the vocabulary", field, n)
        if {"visit_occurrence", "visit_detail"} <= {p.name for p in out_dir.iterdir()}:
            for visit, detail, n in visit_detail_hierarchy(
                out_dir, inputs.vocab.athena("concept_ancestor")
            ).iter_rows():
                log.warning(
                    "%d visit_detail rows with concept %d are not under their visit's %d",
                    n,
                    detail,
                    visit,
                )
    if materialized:
        built = {p.name for p in out_dir.iterdir() if p.is_dir()}
        for domain, n in sources.unrouted(out_dir, built).iter_rows():
            log.warning(
                "%d coded events in domain %s are not written: no table built yet", n, domain
            )
        sources.unrouted_events(out_dir, built).sink_parquet(out_dir / sources.UNROUTED_FILE)
        shutil.rmtree(out_dir / sources.EVENTS_DIR, ignore_errors=True)
    return counts


def _memory() -> str:
    """Peak and current resident memory of this process, for the log."""
    peak = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 2**20
    try:
        status = Path("/proc/self/status").read_text()
        current = int(status.split("VmRSS:")[1].split()[0]) / 2**20
        return f"memory {current:.1f} GB, peak {peak:.1f} GB"
    except (OSError, IndexError, ValueError):
        return f"peak memory {peak:.1f} GB"


def _replace_dir(new: Path, target: Path) -> None:
    if target.exists():
        stray = [p.name for p in target.iterdir() if not p.match("part-*.parquet")]
        if stray:
            raise FileExistsError(f"{target} contains files this ETL did not write: {stray[:5]}")
        shutil.rmtree(target)
    new.rename(target)


def check_foreign_keys(out_dir: Path) -> list[str]:
    """Foreign keys between CDM tables present in out_dir (concept FKs need the vocabulary)."""
    out_dir = Path(out_dir)
    present = {p.name for p in out_dir.iterdir() if any(p.glob("part-*.parquet"))}
    problems = []
    for table in sorted(present):
        lf = pl.scan_parquet(out_dir / table / "part-*.parquet")
        for f in TABLES[table]:
            if f.fk is None or f.fk[0] not in present:
                continue
            ref_table, ref_field = f.fk
            ref = pl.scan_parquet(out_dir / ref_table / "part-*.parquet").select(
                pl.col(ref_field).alias(f.name)
            )
            n = (
                lf.filter(pl.col(f.name).is_not_null())
                .join(ref, on=f.name, how="anti")
                .select(pl.len())
                .collect()
                .item()
            )
            if n:
                problems.append(f"{table}.{f.name}: {n} values not in {ref_table}.{ref_field}")
    return problems


CUSTOM_CONCEPT_START = 2_000_000_000


# concept fields that hold domain and relationship concepts, which are never standard
METADATA_CONCEPT_FIELDS = {
    "fact_relationship.domain_concept_id_1",
    "fact_relationship.domain_concept_id_2",
    "fact_relationship.relationship_concept_id",
    "episode_event.episode_event_field_concept_id",
}


def check_concepts(out_dir: Path, concept: pl.LazyFrame) -> tuple[list[str], dict[str, int]]:
    """Concept fields against the vocabulary: every id must exist, and fields other than
    *_source_concept_id must hold valid standard concepts (0 means unmapped and is allowed).

    METADATA_CONCEPT_FIELDS must exist but need not be standard.
    Custom concepts (ids from 2 billion, e.g. CVB's MIMIC4 concepts) missing from `concept` are
    counted separately instead of failing; pass the output concept table, which includes CVB's.
    """
    out_dir = Path(out_dir)
    known = concept.select(
        "concept_id",
        found=pl.lit(True),
        valid_standard=(pl.col("standard_concept") == "S").fill_null(False)
        & pl.col("invalid_reason").is_null(),
    )
    problems, custom = [], {}
    built = (p.name for p in out_dir.iterdir() if any(p.glob("part-*.parquet")))
    for table in sorted(set(built) - VOCABULARY_TABLES):
        lf = pl.scan_parquet(out_dir / table / "part-*.parquet")
        for f in TABLES[table]:
            if f.fk != ("concept", "concept_id"):
                continue
            values = lf.select(concept_id=pl.col(f.name)).filter(pl.col("concept_id") != 0)
            row = (
                values.join(known, on="concept_id", how="left")
                .select(
                    custom=(
                        pl.col("found").is_null() & (pl.col("concept_id") >= CUSTOM_CONCEPT_START)
                    ).sum(),
                    missing=(
                        pl.col("found").is_null() & (pl.col("concept_id") < CUSTOM_CONCEPT_START)
                    ).sum(),
                    non_standard=(
                        pl.col("found").fill_null(False) & ~pl.col("valid_standard")
                    ).sum(),
                )
                .collect()
                .row(0, named=True)
            )
            name = f"{table}.{f.name}"
            if row["missing"]:
                problems.append(f"{name}: {row['missing']} concept ids not in the vocabulary")
            exempt = f.name.endswith("_source_concept_id") or name in METADATA_CONCEPT_FIELDS
            if row["non_standard"] and not exempt:
                problems.append(f"{name}: {row['non_standard']} non-standard or invalid concepts")
            if row["custom"]:
                custom[name] = row["custom"]
    return problems, custom


def visit_detail_hierarchy(out_dir: Path, concept_ancestor: pl.LazyFrame) -> pl.DataFrame:
    """visit_detail rows whose concept is neither their visit's concept nor a descendant of it.

    The CDM asks that visit_detail_concept_id descend from visit_concept_id. This is reported
    rather than enforced: Emergency Room and Inpatient Visit (262) has no descendants in the
    vocabulary, so the ED and ward parts of such visits cannot satisfy it; see
    docs/etl/visit_detail.md.
    """
    out_dir = Path(out_dir)
    pairs = (
        pl.scan_parquet(out_dir / "visit_detail" / "part-*.parquet")
        .join(
            pl.scan_parquet(out_dir / "visit_occurrence" / "part-*.parquet").select(
                "visit_occurrence_id", "visit_concept_id"
            ),
            on="visit_occurrence_id",
        )
        .group_by("visit_concept_id", "visit_detail_concept_id")
        .len()
    )
    under = concept_ancestor.select(
        visit_concept_id="ancestor_concept_id", visit_detail_concept_id="descendant_concept_id"
    )
    return (
        pairs.filter(pl.col("visit_concept_id") != pl.col("visit_detail_concept_id"))
        .join(under, on=["visit_concept_id", "visit_detail_concept_id"], how="anti")
        .sort("len", descending=True)
        .collect()
    )
