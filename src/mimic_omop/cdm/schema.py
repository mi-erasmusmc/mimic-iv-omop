"""Polars schemas for CDM tables, and conformance/validation against the CDM spec."""

import re

import polars as pl

from mimic_omop.cdm.field import Field
from mimic_omop.cdm.spec import TABLES


class CdmValidationError(Exception):
    pass


def dtype(field: Field) -> pl.DataType:
    # CDM 'integer' is stored as Int64 so generated ids can never overflow
    if field.datatype == "integer":
        return pl.Int64()
    if field.datatype == "float":
        return pl.Float64()
    if field.datatype == "date":
        return pl.Date()
    if field.datatype == "datetime":
        return pl.Datetime("us")
    if field.datatype.startswith("varchar"):
        return pl.String()
    raise ValueError(f"unknown CDM datatype {field.datatype!r} for {field.name}")


def schema(table: str) -> pl.Schema:
    return pl.Schema({f.name: dtype(f) for f in TABLES[table]})


def conform(lf: pl.LazyFrame, table: str) -> pl.LazyFrame:
    """Select the table's columns in spec order, cast them, and add missing optional ones as null.

    Columns that are not in the spec raise: they are almost always a typo in a builder.
    """
    fields = TABLES[table]
    present = lf.collect_schema().names()
    unknown = set(present) - {f.name for f in fields}
    if unknown:
        raise CdmValidationError(f"{table}: columns not in CDM spec: {sorted(unknown)}")
    return lf.select(
        (pl.col(f.name) if f.name in present else pl.lit(None)).cast(dtype(f)).alias(f.name)
        for f in fields
    )


def validate(lf: pl.LazyFrame, table: str) -> list[str]:
    """Checks that need no other table: required fields and primary keys non-null, primary keys
    unique, and strings within their varchar length.

    Runs as a single aggregation query, so it can be pointed at a scan of written output.
    """
    checks = {}
    for f in TABLES[table]:
        # the spec does not always mark primary keys as required (vocabulary_id in 5.5)
        if f.required or f.pk:
            checks[f"{f.name}: {{}} null values in required field"] = pl.col(f.name).null_count()
        if f.pk:
            checks[f"{f.name}: {{}} duplicate primary key values"] = (
                pl.len() - pl.col(f.name).n_unique()
            )
        if (m := re.fullmatch(r"varchar\((\d+)\)", f.datatype)) and (n := int(m[1])):
            checks[f"{f.name}: {{}} values longer than {n} characters"] = (
                pl.col(f.name).str.len_chars() > n
            ).sum()
    if not checks:
        return []
    row = lf.select(**checks).collect().row(0, named=True)
    return [f"{table}.{msg.format(n)}" for msg, n in row.items() if n]
