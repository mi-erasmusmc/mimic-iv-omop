"""Check a featurize.py run against a direct computation for a sample of labels of one task.

Usage: uv run python check_features.py <MEDS dataset> <features dir> <task> [--n 200]
"""

import argparse
import os
from pathlib import Path

import featurize
import numpy as np
import polars as pl
import scipy.sparse as sp


def expected(events: pl.DataFrame, subject: int, t, names: dict[str, int]) -> dict[int, float]:
    want = {}
    ev = events.filter(pl.col("subject_id") == subject, pl.col("time") <= t)
    for window, delta in featurize.WINDOWS.items():
        w = ev if delta is None else ev.filter(pl.col("time") > t - delta)
        for (code,), g in w.group_by("code"):
            if (k := f"{code}/count/{window}") in names:
                want[names[k]] = g.height
            vals = g["numeric_value"].drop_nulls().cast(pl.Float64)
            if len(vals) and f"{code}/sum/{window}" in names:
                for agg, x in (("sum", vals.sum()), ("min", vals.min()), ("max", vals.max())):
                    want[names[f"{code}/{agg}/{window}"]] = x
    static = events.filter(pl.col("subject_id") == subject, pl.col("time").is_null())
    for (code,), g in static.group_by("code"):
        if (k := f"{code}/present/static") in names:
            want[names[k]] = 1.0
            first = g["numeric_value"].drop_nulls()
            if len(first) and (k := f"{code}/first/static") in names:
                want[names[k]] = float(first[0])
    return want


def check(dataset: Path, features: Path, task: str, n: int = 200, seed: int = 0) -> None:
    table = pl.read_parquet(features / "features.parquet")
    names = dict(zip(table["name"], table["column"], strict=True))
    rng = np.random.default_rng(seed)
    bad = 0
    for split in featurize.SPLITS:
        X = sp.load_npz(features / task / f"{split}.npz").tocsr()
        labels = pl.read_parquet(features / task / f"{split}_labels.parquet")
        rows = rng.choice(labels.height, size=min(n, labels.height), replace=False)
        subjects = labels[rows.tolist()]["subject_id"].unique()
        events = (
            pl.scan_parquet(dataset / "data" / split / "*.parquet")
            .filter(pl.col("subject_id").is_in(subjects.implode()))
            .select("subject_id", "time", "code", "numeric_value")
            .collect()
        )
        for i in rows:
            s, t = labels["subject_id"][int(i)], labels["prediction_time"][int(i)]
            r = X[int(i)]
            got = dict(zip(r.indices.tolist(), r.data.tolist(), strict=True))
            want = expected(events, s, t, names)
            if set(got) != set(want) or any(
                not np.isclose(got[k], want[k], rtol=1e-5, atol=1e-3) for k in want
            ):
                bad += 1
        print(f"{split}: {len(rows)} labels checked, {bad} differ so far", flush=True)
    if bad:
        raise SystemExit(f"{bad} labels differ")


if __name__ == "__main__":
    os.environ.setdefault("POLARS_MAX_THREADS", "16")
    p = argparse.ArgumentParser()
    p.add_argument("dataset", type=Path)
    p.add_argument("features", type=Path)
    p.add_argument("task")
    p.add_argument("--n", type=int, default=200)
    a = p.parse_args()
    check(a.dataset, a.features, a.task, a.n)
