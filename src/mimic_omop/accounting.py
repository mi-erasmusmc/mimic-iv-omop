"""Conservation of the coded sources in a built CDM (evaluation E1, docs/evaluation.md).

Every row of a coded source table should either become CDM records or be left out for a
documented reason (DROPPED). The comparison is made per source code, as written to the CDM's
`*_source_value`, so a difference points at the codes it concerns:

- source rows are counted in the converted source parquet;
- CDM rows are counted through the event ids (prefix * 10^15 + row key * 10 + k): k numbers the
  records of one source row from 0, so the record with k = 0 stands for its row, whichever table
  it was routed to. Rows routed to a domain whose table is not built yet are counted from the
  events the run keeps for them (`<cdm>/.unrouted.parquet`);
- a row is mapped if its records have a standard concept, and linked if they have a visit.
"""

from dataclasses import dataclass
from pathlib import Path

import polars as pl

from mimic_omop.etl import drug_events, ed
from mimic_omop.etl.events import DOMAIN_TABLE, EVENT_SCHEMA, PREFIX, SOURCE_VALUE_LENGTH
from mimic_omop.etl.sources import UNROUTED_FILE


def _strip(column: str) -> pl.Expr:
    return pl.col(column).str.strip_chars()


def _text(expr: pl.Expr) -> pl.Expr:
    return expr.str.slice(0, SOURCE_VALUE_LENGTH)


def _item() -> pl.Expr:
    return pl.col("itemid").cast(pl.String)


@dataclass(frozen=True)
class Counted:
    table: str  # converted source table
    code: pl.Expr  # the source code as the ETL writes it to *_source_value
    kept: pl.Expr | None = None  # rows that become records; the others are dropped by design
    dropped: str = ""  # why the other rows are dropped
    # a wide table, each non-empty value of these columns being one record (code: the column)
    values: tuple[str, ...] = ()
    # MIMIC-IV-ED: rows of patients who are not in MIMIC-IV are dropped
    known_persons: bool = False


UNKNOWN_PERSON = "patient not in MIMIC-IV (76 ED stays)"


def _ed(table: str, code: pl.Expr, kept: pl.Expr | None = None, dropped: str = "", values=()):
    reason = "; ".join(r for r in (dropped, UNKNOWN_PERSON) if r)
    return Counted(table, code, kept, reason, tuple(values), known_persons=True)


COUNTED = {
    "diagnoses_icd": Counted("hosp/diagnoses_icd", _strip("icd_code")),
    "procedures_icd": Counted("hosp/procedures_icd", _strip("icd_code")),
    "hcpcsevents": Counted("hosp/hcpcsevents", _strip("hcpcs_cd")),
    "drgcodes": Counted("hosp/drgcodes", pl.col("drg_type") + ":" + pl.col("drg_code")),
    "labevents": Counted("hosp/labevents", _item()),
    "prescriptions": Counted("hosp/prescriptions", _text(pl.col("drug"))),
    "emar": Counted(
        "hosp/emar",
        _text(pl.col("medication")),
        drug_events.administered(pl.col("event_txt")),
        "eMAR action other than an administration (missed, held, refused, ...)",
    ),
    "inputevents": Counted(
        "icu/inputevents",
        _item(),
        ~pl.col("statusdescription").is_in(drug_events.NOT_GIVEN_INPUT_STATUS).fill_null(False),
        "ICU input rewritten (superseded by a corrected row)",
    ),
    "chartevents": Counted("icu/chartevents", _item()),
    "procedureevents": Counted("icu/procedureevents", _item()),
    # within a group of one item, the first row of each (patient, value) stands for its repeats
    "datetimeevents": Counted(
        "icu/datetimeevents",
        _item(),
        pl.struct("subject_id", "value").is_first_distinct(),
        "date charted again (one record per patient, item and date)",
    ),
    "ed_diagnosis": _ed("ed/diagnosis", _strip("icd_code")),
    "ed_triage": _ed("ed/triage", pl.col("item"), values=ed.TRIAGE),
    "ed_vitalsign": _ed("ed/vitalsign", pl.col("item"), values=ed.VITALSIGN),
    "ed_medrecon": _ed(
        "ed/medrecon",
        _text(pl.col("name")),
        pl.col("etc_rn") == 1,
        "repeat of a medication for another drug class (etc_rn > 1)",
    ),
    "ed_pyxis": _ed(
        "ed/pyxis",
        _text(pl.col("name")),
        pl.col("gsn_rn") == 1,
        "repeat of a dispensed medication for another GSN (gsn_rn > 1)",
    ),
    "outputevents": Counted("icu/outputevents", _item()),
    "omr": Counted("hosp/omr", _text(_strip("result_name"))),
    # a row is a susceptibility (coded by antibiotic) or else a test result (coded by test)
    "microbiologyevents": Counted(
        "hosp/microbiologyevents", pl.coalesce("ab_itemid", "test_itemid").cast(pl.String)
    ),
}

# CDM event tables: (id, concept, source value), e.g. drug_exposure_id, drug_concept_id, ...
EVENT_TABLES = {
    table: (
        f"{table}_id",
        f"{table.split('_')[0]}_concept_id",
        f"{table.split('_')[0]}_source_value",
    )
    for table in DOMAIN_TABLE.values()
    if table != "specimen"
}

SOURCE_OF_PREFIX = {prefix: name for name, prefix in PREFIX.items()}
KEY = ["source", "code"]
COUNTS = ["source_rows", "kept_rows", "cdm_rows", "pending_rows", "mapped_rows", "linked_rows"]

# Each source and each CDM table is aggregated and collected on its own, and the small results
# are combined eagerly: the streaming engine keeps each aggregation within a few GB, while one
# plan over all tables (or naming sources before aggregating) does not.


def _counted_rows(source_dir: Path, counted: Counted) -> pl.LazyFrame:
    """The counted rows of a source table: its rows, or the values of a wide table."""
    lf = pl.scan_parquet(Path(source_dir) / f"{counted.table}.parquet")
    if counted.values:
        text = pl.col("value").str.strip_chars()
        lf = (
            lf.with_columns(pl.col(counted.values).cast(pl.String))
            .unpivot(index="subject_id", on=list(counted.values), variable_name="item")
            .filter(text.is_not_null() & (text != ""))
        )
    if counted.known_persons:
        known = pl.scan_parquet(Path(source_dir) / "hosp/patients.parquet").select(
            "subject_id", known=pl.lit(True)
        )
        lf = lf.join(known, on="subject_id", how="left")
    return lf


def source_counts(source_dir: Path) -> pl.DataFrame:
    """(source, code, source_rows, kept_rows)."""
    parts = []
    for name, counted in COUNTED.items():
        if not (Path(source_dir) / f"{counted.table}.parquet").exists():
            continue  # an optional module (MIMIC-IV-ED) that was not converted
        # a literal condition would be summed once per group, not per row
        conditions = [counted.kept] if counted.kept is not None else []
        if counted.known_persons:
            conditions.append(pl.col("known").fill_null(False))
        kept = pl.all_horizontal(conditions).sum() if conditions else pl.len()
        parts.append(
            _counted_rows(source_dir, counted)
            .group_by(code=counted.code)
            .agg(source_rows=pl.len(), kept_rows=kept)
            .collect()
            .with_columns(source=pl.lit(name))
        )
    return pl.concat(parts)


def _rows(lf: pl.LazyFrame, event_id: str, concept: str, source_value: str) -> pl.DataFrame:
    """Per source prefix and code, counts of source rows (records with k = 0)."""
    return (
        lf.filter(pl.col(event_id) % 10 == 0)
        .group_by(prefix=pl.col(event_id) // 10**15, code=pl.col(source_value))
        .agg(
            rows=pl.len(),
            mapped_rows=(pl.col(concept) != 0).sum(),
            linked_rows=pl.col("visit_occurrence_id").is_not_null().sum(),
        )
        .collect()
    )


def _event_table(cdm_dir: Path, table: str) -> pl.LazyFrame:
    lf = pl.scan_parquet(cdm_dir / table / "part-*.parquet")
    if table == "episode":
        # an episode has no visit field; it has the visit of the condition record of its row
        visits = pl.scan_parquet(cdm_dir / "condition_occurrence" / "part-*.parquet").select(
            row=pl.col("condition_occurrence_id") // 10, visit_occurrence_id="visit_occurrence_id"
        )
        lf = lf.with_columns(row=pl.col("episode_id") // 10).join(
            visits.unique("row"), on="row", how="left"
        )
    return lf


def cdm_counts(cdm_dir: Path) -> pl.DataFrame:
    """(source, code, cdm_rows, pending_rows, mapped_rows, linked_rows)."""
    cdm_dir = Path(cdm_dir)
    built = [t for t in EVENT_TABLES if any((cdm_dir / t).glob("part-*.parquet"))]
    parts = [
        _rows(_event_table(cdm_dir, t), *EVENT_TABLES[t]).rename({"rows": "cdm_rows"})
        for t in built
    ]
    if (cdm_dir / UNROUTED_FILE).exists():
        pending = pl.scan_parquet(cdm_dir / UNROUTED_FILE, schema=EVENT_SCHEMA)
        parts.append(
            _rows(pending, "event_id", "concept_id", "source_value").select(
                "prefix", "code", pending_rows="rows"
            )
        )
    return (
        pl.concat(parts, how="diagonal")
        .group_by("prefix", "code")
        .agg(pl.col("cdm_rows", "pending_rows", "mapped_rows", "linked_rows").sum())
        .with_columns(source=pl.col("prefix").replace_strict(SOURCE_OF_PREFIX, default=None))
        .drop("prefix")
    )


def codes(source_dir: Path, cdm_dir: Path) -> pl.DataFrame:
    """Per source and code: rows in the source, kept, written to the CDM, routed to a domain
    without a table yet, and missing (kept but not accounted for; negative if the CDM has more)."""
    return (
        source_counts(source_dir)
        .join(cdm_counts(cdm_dir), on=KEY, how="full", coalesce=True, nulls_equal=True)
        .with_columns(pl.col(COUNTS).fill_null(0).cast(pl.Int64))
        .with_columns(missing=pl.col("kept_rows") - pl.col("cdm_rows") - pl.col("pending_rows"))
        .select(*KEY, *COUNTS, "missing")
        .sort(KEY, nulls_last=True)
    )


def summary(per_code: pl.DataFrame) -> pl.DataFrame:
    """Per source: row accounting, codes that differ, mapping coverage and visit linkage."""
    return (
        per_code.group_by("source")
        .agg(
            pl.col("source_rows", "kept_rows", "cdm_rows", "pending_rows", "missing").sum(),
            dropped_rows=(pl.col("source_rows") - pl.col("kept_rows")).sum(),
            codes=pl.len(),
            codes_differing=(pl.col("missing") != 0).sum(),
            mapped_rows=pl.col("mapped_rows").sum(),
            mapped_codes=(pl.col("mapped_rows") > 0).sum(),
            linked_rows=pl.col("linked_rows").sum(),
        )
        .with_columns(
            mapped_pct=(100 * pl.col("mapped_rows") / pl.col("cdm_rows")).round(1),
            mapped_codes_pct=(100 * pl.col("mapped_codes") / pl.col("codes")).round(1),
            linked_pct=(100 * pl.col("linked_rows") / pl.col("cdm_rows")).round(1),
            dropped_reason=pl.col("source").replace_strict(
                {n: c.dropped for n, c in COUNTED.items()}, default=""
            ),
        )
        .sort(pl.col("source").replace_strict(PREFIX, default=None))
    )


def unmapped(per_code: pl.DataFrame, n: int = 20) -> pl.DataFrame:
    """The n most frequent codes without a standard concept, per source."""
    return (
        per_code.with_columns(
            share_of_source_pct=(
                100 * pl.col("cdm_rows") / pl.col("cdm_rows").sum().over("source")
            ).round(2)
        )
        .filter(pl.col("cdm_rows") > 0, pl.col("mapped_rows") == 0)
        .sort("cdm_rows", descending=True)
        .group_by("source", maintain_order=True)
        .head(n)
        .select("source", "code", "cdm_rows", "share_of_source_pct")
    )


def account(source_dir: Path, cdm_dir: Path, out_dir: Path) -> pl.DataFrame:
    """Write codes.parquet, summary.csv and unmapped.csv to out_dir; return the summary."""
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    per_code = codes(source_dir, cdm_dir)
    per_code.write_parquet(out_dir / "codes.parquet")
    result = summary(per_code)
    result.write_csv(out_dir / "summary.csv")
    unmapped(per_code).write_csv(out_dir / "unmapped.csv")
    return result
