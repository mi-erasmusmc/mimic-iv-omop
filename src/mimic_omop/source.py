"""MIMIC-IV source data: conversion of the PhysioNet csv.gz files to typed parquet, and scanning.

Types are assigned by explicit rule rather than inference, so a column never changes type
between the demo and the full data (inference turns `ndc` into an integer, for example).
"""

import re
from pathlib import Path

import polars as pl

INT_COLUMNS = {
    "subject_id", "hadm_id", "stay_id", "transfer_id", "itemid", "caregiver_id",
    "labevent_id", "specimen_id", "micro_specimen_id", "microevent_id", "spec_itemid",
    "test_itemid", "org_itemid", "ab_itemid", "pharmacy_id", "orderid", "linkorderid",
    "seq_num", "icd_version", "anchor_age", "anchor_year", "hospital_expire_flag",
    "emar_seq", "poe_seq", "test_seq", "isolate_num", "warning",
    "med_rn", "gsn_rn", "etc_rn",  # MIMIC-IV-ED
}  # fmt: skip
FLOAT_COLUMNS = {
    "valuenum", "amount", "rate", "patientweight", "totalamount", "originalamount",
    "originalrate", "ref_range_lower", "ref_range_upper", "dilution_value", "los",
}  # fmt: skip
# `value` is free text in chartevents but numeric in these tables
ED_VITALS = {"temperature", "heartrate", "resprate", "o2sat", "sbp", "dbp"}  # pain is free text
TABLE_FLOAT_COLUMNS = {
    "outputevents": {"value"},
    "procedureevents": {"value"},
    "triage": {*ED_VITALS, "acuity"},
    "vitalsign": ED_VITALS,
}
# date-valued columns; some are written with a 00:00:00 time part, so only the date is parsed
DATE_COLUMNS = {"dod", "chartdate", "storedate"}
DATETIME_FORMAT = "%Y-%m-%d %H:%M:%S%.f"  # fractional seconds are optional


def column_type(table: str, column: str) -> pl.DataType:
    if column in INT_COLUMNS:
        return pl.Int64()
    if column in FLOAT_COLUMNS or column in TABLE_FLOAT_COLUMNS.get(table, ()):
        return pl.Float64()
    if column in DATE_COLUMNS:
        return pl.Date()
    if column.endswith("time"):
        return pl.Datetime("us")
    return pl.String()


def cast_expr(table: str, column: str) -> pl.Expr:
    target = column_type(table, column)
    col = pl.col(column)
    if target == pl.Date():
        return col.str.slice(0, 10).str.to_date("%Y-%m-%d")
    if target == pl.Datetime("us"):
        return col.str.to_datetime(DATETIME_FORMAT, time_unit="us")
    return col.cast(target)


def convert_file(src: Path, dest: Path, table: str) -> int:
    """Stream one csv.gz to parquet with the streaming engine. Raises on any unparseable value."""
    lf = pl.scan_csv(
        src, infer_schema=False, with_column_names=lambda cols: [c.lower() for c in cols]
    )
    dest.parent.mkdir(parents=True, exist_ok=True)
    tmp = dest.with_suffix(".parquet.tmp")
    try:
        lf.select(cast_expr(table, c) for c in lf.collect_schema().names()).sink_parquet(tmp)
    except BaseException:
        tmp.unlink(missing_ok=True)
        raise
    tmp.replace(dest)
    return pl.scan_parquet(dest).select(pl.len()).collect().item()


def convert(src_dir: Path, dest_dir: Path, tables: list[str] | None = None) -> dict[str, int]:
    """Convert `<module>/<table>.csv.gz` files under src_dir to `<module>/<table>.parquet`."""
    found = {
        f"{p.parent.name}/{p.name.removesuffix('.csv.gz')}": p
        for p in sorted(Path(src_dir).glob("*/*.csv.gz"))
    }
    if tables is not None:
        missing = set(tables) - set(found)
        if missing:
            raise FileNotFoundError(f"not found under {src_dir}: {sorted(missing)}")
        found = {t: found[t] for t in tables}
    counts = {}
    for name, path in found.items():
        table = name.split("/")[1]
        counts[name] = convert_file(path, Path(dest_dir) / f"{name}.parquet", table)
    return counts


class Source:
    """Typed parquet MIMIC-IV, scanned lazily as `module/table` (e.g. `hosp/patients`).

    A Source can be restricted to one bucket of persons (subject_id % n == k); tables without a
    subject_id (dictionaries) are returned whole. All CDM logic is per person, so buckets can be
    processed independently.
    """

    def __init__(self, root: Path, bucket: tuple[int, int] | None = None):
        self.root = Path(root)
        self.bucket = bucket

    def for_bucket(self, k: int, n: int) -> "Source":
        return Source(self.root, (k, n))

    def has(self, name: str) -> bool:
        """Whether table `name` was converted (the MIMIC-IV-ED module is optional)."""
        return (self.root / f"{name}.parquet").exists()

    def scan(self, name: str) -> pl.LazyFrame:
        if not re.fullmatch(r"[a-z]+/[a-z_]+", name):
            raise ValueError(f"source table must look like 'hosp/patients', got {name!r}")
        return in_bucket(pl.scan_parquet(self.root / f"{name}.parquet"), "subject_id", self.bucket)


def in_bucket(lf: pl.LazyFrame, column: str, bucket: tuple[int, int] | None) -> pl.LazyFrame:
    """Restrict lf to one person bucket if it has the person column; otherwise return it whole."""
    if bucket is None or column not in lf.collect_schema().names():
        return lf
    k, n = bucket
    return lf.filter(pl.col(column) % n == k)
