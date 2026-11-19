"""Agreement between a derived table computed on the source and on the CDM."""

import polars as pl

STATUS = ["identical", "different", "source_only", "cdm_only"]


def kappa(a: pl.Series, b: pl.Series) -> float | None:
    """Cohen's kappa of two binary or categorical series of equal length."""
    df = pl.DataFrame({"a": a, "b": b}).drop_nulls()
    n = df.height
    if n == 0:
        return None
    observed = (df["a"] == df["b"]).mean()
    pa = df["a"].value_counts(normalize=True, name="pa").rename({"a": "v"})
    pb = df["b"].value_counts(normalize=True, name="pb").rename({"b": "v"})
    expected = pa.join(pb, on="v").select((pl.col("pa") * pl.col("pb")).sum()).item()
    return None if expected == 1 else round((observed - expected) / (1 - expected), 4)


def paired(source: pl.DataFrame, cdm: pl.DataFrame, key: list[str]) -> pl.DataFrame:
    """Full join on key; columns of the CDM side get the suffix `_cdm`."""
    return source.join(cdm, on=key, how="full", coalesce=True, suffix="_cdm")


def values(
    source: pl.DataFrame, cdm: pl.DataFrame, key: list[str], columns: list[str]
) -> pl.DataFrame:
    """Per column: how many keys have the same value on both sides, a different one, or a value
    on one side only, and the median absolute difference where they differ."""
    p = paired(source, cdm, key)
    rows = []
    for c in columns:
        # mimic-code rounds some values to DECIMAL
        s, d = pl.col(c).cast(pl.Float64), pl.col(f"{c}_cdm").cast(pl.Float64)
        status = (
            pl.when(s.is_null() & d.is_null())
            .then(None)
            .when(d.is_null())
            .then(pl.lit("source_only"))
            .when(s.is_null())
            .then(pl.lit("cdm_only"))
            .when((s - d).abs() <= 1e-9 * (1 + s.abs()))
            .then(pl.lit("identical"))
            .otherwise(pl.lit("different"))
        )
        r = p.select(status=status, diff=(s - d).abs(), rel=(s - d).abs() / s.abs()).drop_nulls(
            "status"
        )
        counts = dict(r.group_by("status").len().iter_rows())
        n = r.height
        differ = r.filter(pl.col("status") == "different")
        rows.append(
            {
                "variable": c,
                "with_value": n,
                **{k: counts.get(k, 0) for k in STATUS},
                "identical_pct": round(100 * counts.get("identical", 0) / n, 2) if n else None,
                "median_abs_diff": differ["diff"].median(),
                "median_rel_diff": differ["rel"].median(),
            }
        )
    return pl.DataFrame(rows)


def flags(
    source: pl.DataFrame, cdm: pl.DataFrame, key: list[str], columns: list[str]
) -> pl.DataFrame:
    """Per binary or categorical column: positives on each side, agreement and kappa, over the
    keys of the source (a key missing on the CDM side counts as 0)."""
    p = source.join(cdm, on=key, how="left", suffix="_cdm")
    rows = []
    for c in columns:
        a = p[c].fill_null(0)
        b = p[f"{c}_cdm"].fill_null(0)
        rows.append(
            {
                "variable": c,
                "keys": p.height,
                "source_positive": int((a > 0).sum()),
                "cdm_positive": int((b > 0).sum()),
                "source_only": int(((a > 0) & (b == 0)).sum()),
                "cdm_only": int(((a == 0) & (b > 0)).sum()),
                "agreement_pct": round(100 * (a == b).mean(), 3),
                "kappa": kappa(a, b),
            }
        )
    return pl.DataFrame(rows)
