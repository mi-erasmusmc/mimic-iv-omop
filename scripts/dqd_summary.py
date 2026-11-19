"""Summarise a Data Quality Dashboard result file: overview, errors, and failed checks.

Usage: uv run python scripts/dqd_summary.py [data/dqd/dqd/dqd.json]
"""

import json
import sys

import polars as pl


def main(path: str) -> None:
    result = json.load(open(path))
    overview = {k: v[0] for k, v in result["Overview"].items()}
    print(
        f"{overview['countPassed']} of {overview['countTotal']} checks passed "
        f"({overview['percentPassed']}% of those applicable); "
        f"{overview['countThresholdFailed']} failed, {overview['countErrorFailed']} errors in total"
    )
    checks = pl.DataFrame(result["CheckResults"], infer_schema_length=None).with_columns(
        status=pl.when(pl.col("isError") == 1)
        .then(pl.lit("error"))
        .when(pl.col("notApplicable") == 1)
        .then(pl.lit("n/a"))
        .when(pl.col("failed") == 1)
        .then(pl.lit("failed"))
        .otherwise(pl.lit("passed"))
    )
    pl.Config.set_tbl_rows(100)
    pl.Config.set_tbl_width_chars(200)
    pl.Config.set_fmt_str_lengths(90)
    print(checks.group_by("status").len().sort("status"))
    errors = checks.filter(pl.col("status") == "error")
    if errors.height:
        print("Errors (first line of each distinct message):")
        print(
            errors.with_columns(message=pl.col("error").str.split("\n").list.first())
            .group_by("checkName", "message")
            .agg(tables=pl.col("cdmTableName").unique().sort().str.join(", "))
        )
    print("Failed checks:")
    print(
        checks.filter(pl.col("status") == "failed")
        .select(
            "checkName",
            "cdmTableName",
            "cdmFieldName",
            "conceptId",
            "numViolatedRows",
            pct=(pl.col("pctViolatedRows") * 100).round(2),
            threshold="thresholdValue",
        )
        .sort("checkName", "cdmTableName", "cdmFieldName")
    )


if __name__ == "__main__":
    main(sys.argv[1] if len(sys.argv) > 1 else "data/dqd/dqd/dqd.json")
