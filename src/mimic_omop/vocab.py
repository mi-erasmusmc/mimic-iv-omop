"""Vocabulary inputs: Athena vocabulary tables and the Tufts CVB MIMIC mappings.

Layout of a vocab directory:
    athena/<table>.parquet       loaded with `load_athena` from an Athena download
    cvb_mimic_mapping.parquet    fetched with `fetch_cvb`
"""

import io
import json
import logging
import urllib.request
from importlib.resources import files
from pathlib import Path

import polars as pl

from mimic_omop.cdm.schema import dtype
from mimic_omop.cdm.spec import TABLES

log = logging.getLogger(__name__)

CVB_REPO = "TuftsCTSI/CVB"
CVB_REF = "bf5701cf37b1ceaa3114eaa493dfba9b6b2c8ec1"  # main, 2026-06-19
CVB_MAPPING_PATH = "MIMIC/Mappings/mimic_mapping.csv"
# CVB's MIMIC4 vocabulary in OMOP form, written to the output vocabulary tables
CVB_ONTOLOGY = (
    "concept_delta",
    "concept_relationship_delta",
    "vocabulary_delta",
    "concept_class_delta",
)

VALUE_DOMAINS = ("Observation", "Measurement")  # domains whose tables have value_as_concept_id

# vocabularies the ETL or the CVB mappings use; some (UCUM, Type Concept, ...) are not offered
# for selection on Athena because every download includes them
NEEDED_VOCABULARIES = (
    "SNOMED", "ICD9CM", "ICD10CM", "ICD9Proc", "ICD10PCS", "HCPCS", "CPT4",
    "RxNorm", "RxNorm Extension", "NDC", "CVX", "ATC", "LOINC", "UCUM",
    "Gender", "Race", "Ethnicity", "Visit", "CMS Place of Service", "Medicare Specialty", "NUCC",
    "DRG", "PPI", "Nebraska Lexicon", "OMOP Extension",
    # targets of ICD/CPT/HCPCS metastasis codes; Athena leaves out relationships into
    # vocabularies that are not in the bundle, so without it those codes are silently unmapped
    "Cancer Modifier",
    "Type Concept", "Condition Status", "CDM",
)  # fmt: skip


def fetch_cvb(dest: Path, ref: str = CVB_REF) -> Path:
    """Download the CVB MIMIC curation file at a pinned commit and store it as parquet.

    We use mimic_mapping.csv rather than Ontology/source_to_concept_map.csv because only the former
    says which MIMIC field a source code belongs to; the same string (e.g. 'UNKNOWN') occurs in
    several fields with different meanings.
    """
    base = f"https://raw.githubusercontent.com/{CVB_REPO}/{ref}"
    url = f"{base}/{CVB_MAPPING_PATH}"
    dest = Path(dest)
    dest.mkdir(parents=True, exist_ok=True)
    with urllib.request.urlopen(url) as resp:
        raw = resp.read()
    df = pl.read_csv(raw, infer_schema=False)
    df.write_parquet(dest / "cvb_mimic_mapping.parquet")
    (dest / "cvb").mkdir(exist_ok=True)
    for name in CVB_ONTOLOGY:
        with urllib.request.urlopen(f"{base}/MIMIC/Ontology/{name}.csv") as resp:
            pl.read_csv(resp.read(), infer_schema=False).write_parquet(
                dest / "cvb" / f"{name}.parquet"
            )
    (dest / "cvb_mimic_mapping.json").write_text(
        json.dumps({"repo": CVB_REPO, "ref": ref, "path": CVB_MAPPING_PATH, "rows": df.height})
    )
    return dest / "cvb_mimic_mapping.parquet"


def load_athena(src_dir: Path, dest: Path) -> dict[str, int]:
    """Convert an unzipped Athena download (tab-separated, unquoted, YYYYMMDD dates) to parquet.

    Every file named after a CDM vocabulary table is typed by the CDM spec; others are skipped.
    """
    out = Path(dest) / "athena"
    out.mkdir(parents=True, exist_ok=True)
    counts = {}
    for path in sorted(Path(src_dir).glob("*.csv")):
        table = path.stem.lower()
        if table not in TABLES:
            # CONCEPT_CPT4.csv is the CPT4 tool's working file; anything else is unexpected
            level = logging.INFO if table == "concept_cpt4" else logging.WARNING
            log.log(level, "skipping %s: not a CDM table", path.name)
            continue
        lf = pl.scan_csv(
            path,
            separator="\t",
            quote_char=None,
            infer_schema=False,
            with_column_names=lambda cols: [c.lower() for c in cols],
        )
        fields = {f.name: f for f in TABLES[table]}
        names = lf.collect_schema().names()
        unknown = set(names) - set(fields)
        if unknown:
            raise ValueError(f"{path.name}: columns not in the CDM spec: {sorted(unknown)}")
        exprs = []
        for name in names:
            target = dtype(fields[name])
            col = pl.col(name)
            exprs.append(col.str.to_date("%Y%m%d") if target == pl.Date() else col.cast(target))
        lf.select(exprs).sink_parquet(out / f"{table}.parquet")
        counts[table] = pl.scan_parquet(out / f"{table}.parquet").select(pl.len()).collect().item()
        log.info("%s: %d rows", table, counts[table])
    if "concept" in counts:
        for name in missing_vocabularies(pl.scan_parquet(out / "concept.parquet")):
            log.warning("vocabulary %r has no concepts in this download", name)
    return counts


def missing_vocabularies(concept: pl.LazyFrame) -> list[str]:
    present = set(concept.select(pl.col("vocabulary_id").unique()).collect().to_series())
    return [v for v in NEEDED_VOCABULARIES if v not in present]


class Vocab:
    def __init__(self, root: Path):
        self.root = Path(root)
        self._maps: dict[tuple, pl.DataFrame] = {}
        self._cvb: pl.DataFrame | None = None

    def has_athena(self) -> bool:
        return (self.root / "athena" / "concept.parquet").exists()

    def athena(self, table: str) -> pl.LazyFrame:
        path = self.root / "athena" / f"{table}.parquet"
        if not path.exists():
            raise FileNotFoundError(f"{path} not found; run `mimic-omop load-athena` first")
        return pl.scan_parquet(path)

    def cvb_ontology(self, name: str) -> pl.LazyFrame:
        """One of CVB's vocabulary files (CVB_ONTOLOGY), as text columns."""
        path = self.root / "cvb" / f"{name}.parquet"
        if not path.exists():
            raise FileNotFoundError(f"{path} not found; run `mimic-omop fetch-cvb` again")
        return pl.scan_parquet(path)

    def cvb_version(self) -> dict:
        return json.loads((self.root / "cvb_mimic_mapping.json").read_text())

    def cvb(self) -> pl.LazyFrame:
        """CVB mappings, with targets resolved to valid standard concepts when Athena is loaded.

        Without Athena (as in CI) the targets are used as CVB wrote them.
        """
        if self._cvb is None:
            raw = without_exclusions(pl.scan_parquet(self.root / "cvb_mimic_mapping.parquet"))
            if self.has_athena():
                raw = resolve_cvb(
                    raw,
                    self.athena("concept"),
                    self.athena("concept_relationship"),
                    self.athena("concept_ancestor"),
                )
            else:
                log.warning("no Athena vocabulary loaded: CVB targets are not checked")
            self._cvb = raw.collect()
        return self._cvb.lazy()

    def lookup(self, source_code_set: str, domains: tuple[str, ...] | None = None) -> pl.LazyFrame:
        """One standard target per source code of a CVB source code set ('Maps to' only).

        Returns source_code, source_concept_id, target_concept_id, target_domain_id. Raises if a
        code has several targets, because joining such a lookup would duplicate source rows; use
        a one-to-many join explicitly where that is intended.
        """
        return cvb_lookup(self.cvb(), source_code_set, domains)

    def code_map(
        self, vocabularies: tuple[str, ...], cvb_set: str | None = None, strip_dots: bool = False
    ) -> pl.LazyFrame:
        """See `code_map`. Computed once per Vocab (it scans all of concept_relationship) and
        held in memory: it has one row per source code, so it is small."""
        key = (vocabularies, cvb_set, strip_dots)
        if key not in self._maps:
            self._maps[key] = code_map(
                self.athena("concept"),
                self.athena("concept_relationship"),
                self.cvb(),
                vocabularies,
                cvb_set,
                strip_dots,
            ).collect()
        return self._maps[key].lazy()

    def cvb_code_map(self, cvb_set: str) -> pl.LazyFrame:
        return cvb_code_map(self.cvb(), cvb_set)

    def unit_map(self) -> pl.LazyFrame:
        if "units" not in self._maps:
            self._maps["units"] = unit_map(self.cvb(), self.athena("concept")).collect()
        return self._maps["units"].lazy()


CVB_CODE = ["source_code_set", "source_code"]
EXCLUSION_KEY = [*CVB_CODE, "relationship_id", "target_concept_id"]


def cvb_exclusions() -> pl.LazyFrame:
    """mappings/cvb_exclusions.csv: CVB relationships known to be wrong, left out of the ETL's
    mappings until CVB corrects them (review R26): the four key columns and a reason."""
    text = files("mimic_omop.mappings").joinpath("cvb_exclusions.csv").read_text()
    return pl.read_csv(io.StringIO(text), infer_schema=False).lazy()


def without_exclusions(cvb: pl.LazyFrame) -> pl.LazyFrame:
    excluded = cvb_exclusions().select(EXCLUSION_KEY)
    key = cvb.select(EXCLUSION_KEY).collect_schema()
    return cvb.join(excluded.cast(dict(key)), on=EXCLUSION_KEY, how="anti", nulls_equal=True)


def resolve_cvb(
    cvb: pl.LazyFrame,
    concept: pl.LazyFrame,
    concept_relationship: pl.LazyFrame,
    concept_ancestor: pl.LazyFrame,
) -> pl.LazyFrame:
    """Replace CVB 'Maps to' / 'Maps to value' targets by valid standard concepts, and map codes
    without one to their broader concepts ('Is a').

    A target that is still standard is kept; one deprecated or de-standardised since the CVB
    refresh is followed through its own 'Maps to'; one with neither (including CVB's custom
    MIMIC4 concepts, which are not in Athena) becomes 0. The target domain is taken from Athena,
    since CVB's copy of it can be stale.

    A code whose 'Maps to' gives no target is mapped uphill (review R29): its 'Is a' targets,
    resolved the same way, are added as 'Maps to' rows. A target that is an ancestor of another
    target of the same code is dropped, so the most specific is used. Of several remaining
    targets, all are used if they are drug ingredients (a combination product: one record per
    ingredient); otherwise the code stays unmapped, since each is a different broader meaning of
    the same item and choosing one would be a local mapping. The 'Is a' rows are kept as they are.
    """
    tid = pl.col("target_concept_id").cast(pl.Int64, strict=False).fill_null(0)
    is_target = (
        pl.col("relationship_id").is_in(["Maps to", "Maps to value"]) & (tid != 0)
    ).fill_null(False)
    standard = concept.filter(
        pl.col("standard_concept") == "S", pl.col("invalid_reason").is_null()
    ).select(tid="concept_id", domain="domain_id", concept_class="concept_class_id")
    maps_to = concept_relationship.filter(
        pl.col("relationship_id") == "Maps to", pl.col("invalid_reason").is_null()
    ).select(tid="concept_id_1", new_tid="concept_id_2")

    def to_standard(rows: pl.LazyFrame) -> tuple[pl.LazyFrame, pl.LazyFrame]:
        """(rows whose target is or maps to a standard concept, now tid; the other rows)."""
        rows = rows.with_columns(tid=tid)
        direct = rows.join(standard, on="tid")
        remapped = (
            rows.join(standard, on="tid", how="anti")
            .join(maps_to, on="tid")
            .join(standard.rename({"tid": "new_tid"}), on="new_tid")
            .with_columns(tid="new_tid")
            .drop("new_tid")
        )
        unresolved = rows.join(standard, on="tid", how="anti").join(
            maps_to.join(standard.rename({"tid": "new_tid"}), on="new_tid"), on="tid", how="anti"
        )
        return pl.concat([direct, remapped], how="diagonal_relaxed"), unresolved

    found, unresolved = to_standard(cvb.filter(is_target))
    dropped = unresolved.with_columns(tid=pl.lit(0, pl.Int64), domain=pl.lit(None, pl.String))

    mapped = found.filter(pl.col("relationship_id") == "Maps to").select(CVB_CODE).unique()
    broader, _ = to_standard(
        cvb.filter(pl.col("relationship_id") == "Is a", tid != 0).join(
            mapped, on=CVB_CODE, how="anti"
        )
    )
    broader = broader.unique([*CVB_CODE, "tid"])
    pairs = broader.select(*CVB_CODE, a="tid").join(broader.select(*CVB_CODE, d="tid"), on=CVB_CODE)
    nested = (
        concept_ancestor.filter(pl.col("min_levels_of_separation") > 0)
        .select(a="ancestor_concept_id", d="descendant_concept_id")
        .join(pairs.select("a", "d").unique(), on=["a", "d"], how="semi")
    )
    ancestors = pairs.join(nested, on=["a", "d"], how="semi").select(*CVB_CODE, tid="a").unique()
    broader = (
        broader.join(ancestors, on=[*CVB_CODE, "tid"], how="anti")
        .filter(
            (pl.len().over(CVB_CODE) == 1)
            | (pl.col("concept_class") == "Ingredient").all().over(CVB_CODE)
        )
        .with_columns(relationship_id=pl.lit("Maps to"))
    )

    resolved = pl.concat([found, dropped, broader], how="diagonal_relaxed").with_columns(
        target_concept_id=pl.col("tid").cast(pl.String), target_domain_id="domain"
    )
    return pl.concat(
        [cvb.filter(~is_target), resolved.select(cvb.collect_schema().names())],
        how="vertical_relaxed",
    )


def cvb_lookup(
    cvb: pl.LazyFrame, source_code_set: str, domains: tuple[str, ...] | None = None
) -> pl.LazyFrame:
    lf = cvb.filter(
        pl.col("source_code_set") == source_code_set,
        pl.col("relationship_id") == "Maps to",
        pl.col("target_concept_id").cast(pl.Int64) != 0,
    )
    if domains is not None:
        lf = lf.filter(pl.col("target_domain_id").is_in(domains))
    df = (
        lf.select(
            pl.col("source_code").str.strip_chars(),
            pl.col("source_concept_id").cast(pl.Int64),
            pl.col("target_concept_id").cast(pl.Int64),
            "target_domain_id",
        )
        .unique()
        .collect()
    )
    dupes = df.filter(pl.col("source_code").is_duplicated())["source_code"].unique().to_list()
    if dupes:
        raise ValueError(f"{source_code_set}: several 'Maps to' targets for {dupes[:10]}")
    return df.lazy()


def cvb_code_map(cvb: pl.LazyFrame, cvb_set: str) -> pl.LazyFrame:
    """All 'Maps to' targets of a CVB source code set, with its 'Maps to value' if any.

    Keyed by source_code; one row per target. Codes CVB lists without a mapping are left out.
    """
    rows = cvb.filter(pl.col("source_code_set") == cvb_set).select(
        pl.col("source_code").str.strip_chars(),
        "relationship_id",
        pl.col("source_concept_id").cast(pl.Int64),
        pl.col("target_concept_id").cast(pl.Int64),
        "target_domain_id",
    )
    values = (
        rows.filter(pl.col("relationship_id") == "Maps to value", pl.col("target_concept_id") != 0)
        .group_by("source_code")
        .agg(value_as_concept_id=pl.col("target_concept_id").min())
    )
    return (
        rows.filter(pl.col("relationship_id") == "Maps to", pl.col("target_concept_id") != 0)
        .drop("relationship_id")
        .unique()
        .join(values, on="source_code", how="left")
    )


def code_map(
    concept: pl.LazyFrame,
    concept_relationship: pl.LazyFrame,
    cvb: pl.LazyFrame,
    vocabularies: tuple[str, ...],
    cvb_set: str | None = None,
    strip_dots: bool = False,
) -> pl.LazyFrame:
    """Source codes of standard vocabularies to standard concepts, Athena first, CVB for the rest.

    Keyed by (vocabulary_id, source_code), with source_concept_id, target_concept_id,
    target_domain_id and value_as_concept_id. A code can have several rows (one per 'Maps to'
    target); codes without any mapping have one row with a null target. Athena is preferred
    because it carries the 'Maps to value' structure; CVB supplies codes Athena does not map, and
    since CVB codes do not name their vocabulary they apply to every vocabulary in `vocabularies`.
    """
    keys = ["vocabulary_id", "source_code"]
    code = pl.col("concept_code")
    source = concept.filter(pl.col("vocabulary_id").is_in(vocabularies)).select(
        "vocabulary_id",
        source_code=code.str.replace_all(".", "", literal=True) if strip_dots else code,
        source_concept_id="concept_id",
    )
    standard = concept.filter(
        pl.col("standard_concept") == "S", pl.col("invalid_reason").is_null()
    ).select("concept_id", "domain_id")
    relationships = concept_relationship.filter(pl.col("invalid_reason").is_null()).join(
        source.select(concept_id_1="source_concept_id"), on="concept_id_1", how="semi"
    )

    def targets(relationship: str) -> pl.LazyFrame:
        return (
            relationships.filter(pl.col("relationship_id") == relationship)
            .join(standard, left_on="concept_id_2", right_on="concept_id")
            .select(source_concept_id="concept_id_1", target="concept_id_2", domain="domain_id")
        )

    athena = (
        source.join(
            targets("Maps to").select(
                "source_concept_id", target_concept_id="target", target_domain_id="domain"
            ),
            on="source_concept_id",
            how="left",
        )
        .join(
            targets("Maps to value")
            .group_by("source_concept_id")
            .agg(value_as_concept_id=pl.col("target").min()),
            on="source_concept_id",
            how="left",
        )
        .with_columns(source=pl.lit("athena"))
    )
    parts = [athena]
    if cvb_set is not None:
        mapped = athena.filter(pl.col("target_concept_id").is_not_null()).select(keys).unique()
        # A CVB code applies to vocabulary V if Athena has it in V, or in none of `vocabularies`;
        # many codes exist in both ICD-9 and ICD-10, and must not borrow each other's mapping.
        in_any = source.select("source_code").unique().with_columns(in_any=True)
        cvb_maps = (
            cvb_code_map(cvb, cvb_set)
            .rename({"source_concept_id": "cvb_source_concept_id"})
            .join(pl.LazyFrame({"vocabulary_id": list(vocabularies)}), how="cross")
            .join(mapped, on=keys, how="anti")
            .join(source, on=keys, how="left")
            .join(in_any, on="source_code", how="left")
            .filter(pl.col("source_concept_id").is_not_null() | pl.col("in_any").is_null())
            .select(
                *keys,
                source_concept_id=pl.coalesce("source_concept_id", "cvb_source_concept_id"),
                target_concept_id="target_concept_id",
                target_domain_id="target_domain_id",
                value_as_concept_id="value_as_concept_id",
                source=pl.lit("cvb"),
            )
        )
        # drop the unmapped Athena row of codes that CVB maps
        parts = [athena.join(cvb_maps.select(keys).unique(), on=keys, how="anti"), cvb_maps]
    return pl.concat(parts, how="diagonal_relaxed").with_columns(
        value_as_concept_id=pl.when(pl.col("target_domain_id").is_in(VALUE_DOMAINS)).then(
            "value_as_concept_id"
        )
    )


def unit_map(cvb: pl.LazyFrame, concept: pl.LazyFrame) -> pl.LazyFrame:
    """MIMIC unit strings to UCUM concepts: unit_source_value -> unit_concept_id.

    In order: CVB's curated unit mappings; an exact match on a valid standard UCUM code (many
    MIMIC units, like g/dL, are written in UCUM already); a case-insensitive match, but only
    where it is unambiguous (MIMIC writes 'ml' for mL, but UCUM codes are case-sensitive and
    'ms' could be millisecond, millisiemens or megasecond).
    """
    curated = (
        cvb_code_map(cvb, "mimiciv_units")
        .filter(pl.col("target_domain_id") == "Unit")
        .select(unit_source_value="source_code", unit_concept_id="target_concept_id")
    )
    ucum = concept.filter(
        pl.col("vocabulary_id") == "UCUM",
        pl.col("standard_concept") == "S",
        pl.col("invalid_reason").is_null(),
    ).select(code="concept_code", unit_concept_id="concept_id")
    exact = ucum.select(unit_source_value="code", unit_concept_id="unit_concept_id")
    unambiguous = (
        ucum.with_columns(key=pl.col("code").str.to_lowercase())
        .filter(pl.len().over("key") == 1)
        .select("key", "unit_concept_id")
    )
    return pl.concat([curated, exact, unambiguous.rename({"key": "unit_source_value"})]).unique(
        "unit_source_value", keep="first", maintain_order=True
    )
