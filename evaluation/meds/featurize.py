"""E3 features: counts and value summaries of each code in windows ending at the prediction time.

MEDS-Tab's definitions (codes seen at least MIN_COUNT times in the training split; per window, the
code's count and its values' sum, minimum and maximum; static codes present), computed directly at
the labels' prediction times, for all tasks in one pass over the data:

- a window is (t - w, t] for prediction time t, and `full` is everything up to t;
- `count` counts events, and `min`/`max` are over the values present;
- events without a time are static: `present` per code, and `first` for its first value.

MEDS-Tab 0.2 (and main of 2026-10-22) differs, which is why this exists besides being faster: it
drops the last event of every (subject, time) and with it every time that has a single event (its
group indices miss a `+ 1`), counts times rather than events, takes min/max with the implicit zeros
of a sparse matrix, and anchors windows at the last event before the prediction time
(docs/evaluation-results.md, E3a).

Per shard, events are sorted by (subject, code, time). A window's events for a (label, code) pair
are a contiguous range found by binary search; counts and sums come from cumulative sums, and
min/max from a sparse table (range queries in constant time).

Output, per task and split: `<out>/<task>/<split>.npz` (CSR, one row per label in the label files'
order, shards in name order), `<split>_labels.parquet` (subject_id, prediction_time,
boolean_value), and `<out>/features.parquet` (column, code, agg, window, train_count).
"""

import argparse
import json
import multiprocessing
import os
import shutil
import time
from concurrent.futures import ProcessPoolExecutor
from datetime import timedelta
from pathlib import Path

import numpy as np
import polars as pl
import scipy.sparse as sp

MIN_COUNT = 10
WINDOWS = {
    "1d": timedelta(days=1),
    "7d": timedelta(days=7),
    "30d": timedelta(days=30),
    "full": None,
}
VALUE_AGGS = ("sum", "min", "max")
SPLITS = ("train", "tuning", "held_out")
LABEL_COLUMNS = ["subject_id", "prediction_time", "boolean_value"]


def vocabulary(dataset: Path, min_count: int = MIN_COUNT) -> pl.DataFrame:
    """Codes with at least `min_count` training events: per code its training event count, value
    count, and whether it is static (no time) or dynamic, with an index for each feature block."""
    train = pl.scan_parquet(dataset / "data/train/*.parquet")
    counts = (
        train.group_by("code", static=pl.col("time").is_null())
        .agg(n=pl.len(), n_values=pl.col("numeric_value").is_not_null().sum())
        .filter(pl.col("n") >= min_count)
        .sort("static", "code")
        .collect()
    )
    return counts.with_columns(
        has_values=pl.col("n_values") >= min_count,
        idx=pl.int_range(pl.len()).over("static"),
    ).with_columns(
        value_idx=pl.when("has_values").then(pl.col("has_values").cum_sum().over("static") - 1)
    )


def feature_table(vocab: pl.DataFrame) -> pl.DataFrame:
    """The columns: per window, a count block and the value blocks; then the static blocks."""
    dynamic = vocab.filter(~pl.col("static"))
    valued = dynamic.filter("has_values")
    static = vocab.filter("static")
    blocks = []
    for window in WINDOWS:
        blocks.append(dynamic.select("code", "n", agg=pl.lit("count"), window=pl.lit(window)))
        for agg in VALUE_AGGS:
            blocks.append(valued.select("code", "n", agg=pl.lit(agg), window=pl.lit(window)))
    blocks.append(static.select("code", "n", agg=pl.lit("present"), window=pl.lit("static")))
    blocks.append(
        static.filter("has_values").select(
            "code", "n", agg=pl.lit("first"), window=pl.lit("static")
        )
    )
    return (
        pl.concat(blocks)
        .rename({"n": "train_count"})
        .with_row_index("column")
        .with_columns(name=pl.format("{}/{}/{}", "code", "agg", "window"))
    )


def offsets(vocab: pl.DataFrame) -> dict[tuple[str, str], int]:
    """Start column of each (window, agg) block, in feature_table's order."""
    n_dynamic = vocab.filter(~pl.col("static")).height
    n_valued = vocab.filter(~pl.col("static") & pl.col("has_values")).height
    static = vocab.filter("static")
    out, at = {}, 0
    for window in WINDOWS:
        out[(window, "count")] = at
        at += n_dynamic
        for agg in VALUE_AGGS:
            out[(window, agg)] = at
            at += n_valued
    out[("static", "present")] = at
    at += static.height
    out[("static", "first")] = at
    out["width"] = at + static.filter("has_values").height
    return out


class SparseTable:
    """Range minimum or maximum over a fixed array in constant time per query."""

    def __init__(self, values: np.ndarray, op):
        self.op = op
        self.levels = [values]
        k = 1
        while 2 * k <= len(values):
            prev = self.levels[-1]
            self.levels.append(op(prev[:-k], prev[k:]))
            k *= 2

    def query(self, lo: np.ndarray, hi: np.ndarray) -> np.ndarray:
        """op over values[lo:hi] for each pair; every range must be non-empty."""
        k = np.floor(np.log2(hi - lo)).astype(np.int64)
        out = np.empty(len(lo), dtype=self.levels[0].dtype)
        for level in np.unique(k):
            m = k == level
            table = self.levels[level]
            out[m] = self.op(table[lo[m]], table[hi[m] - (1 << level)])
        return out


def shard_features(
    events: pl.DataFrame, labels: dict[str, pl.DataFrame], vocab: pl.DataFrame, cols: dict
) -> dict[str, sp.csr_matrix]:
    """Feature rows for each task's labels in one shard."""
    dynamic = vocab.filter(~pl.col("static")).select("code", "idx", "value_idx")
    static_vocab = vocab.filter("static").select("code", "idx", "value_idx")
    ev = (
        events.filter(pl.col("time").is_not_null())
        .join(dynamic, on="code", how="inner")
        .select(
            "subject_id",
            "idx",
            "value_idx",
            t=pl.col("time").dt.epoch("us"),
            v=pl.col("numeric_value").cast(pl.Float64),
        )
        .sort("subject_id", "idx", "t")
    )
    ev = ev.with_columns(group=pl.struct("subject_id", "idx").rle_id())
    groups = ev.group_by("group", maintain_order=True).agg(
        pl.col("subject_id").first(), pl.col("idx").first(), pl.col("value_idx").first()
    )
    g_subject = groups["subject_id"].to_numpy()
    g_idx = groups["idx"].to_numpy()
    g_vidx = groups["value_idx"].fill_null(-1).to_numpy()
    # each subject's groups are contiguous
    subjects, g_start = np.unique(g_subject, return_index=True)
    g_end = np.append(g_start[1:], len(g_subject))

    uniq = np.unique(ev["t"].to_numpy())
    n_ranks = len(uniq)

    def rank_at_or_before(t: np.ndarray) -> np.ndarray:  # -1 when before every time
        return np.searchsorted(uniq, t, side="right") - 1

    stride = n_ranks + 1
    # int64: rle_id's UInt32 group ids times the stride overflow on a real shard
    group = ev["group"].to_numpy().astype(np.int64)
    keys = group * stride + rank_at_or_before(ev["t"].to_numpy())
    valued = ev.filter(pl.col("v").is_not_null() & pl.col("value_idx").is_not_null())
    vgroup = valued["group"].to_numpy().astype(np.int64)
    vkeys = vgroup * stride + rank_at_or_before(valued["t"].to_numpy())
    if np.any(np.diff(keys) < 0) or np.any(np.diff(vkeys) < 0):
        raise ValueError("search keys are not sorted")
    vals = valued["v"].to_numpy()
    csum = np.concatenate([[0.0], np.cumsum(vals)])
    vmin = SparseTable(vals, np.minimum) if len(vals) else None
    vmax = SparseTable(vals, np.maximum) if len(vals) else None

    static = (
        events.filter(pl.col("time").is_null())
        .join(static_vocab, on="code", how="inner")
        .group_by("subject_id", "idx", "value_idx")
        .agg(first=pl.col("numeric_value").drop_nulls().first())
    )

    out = {}
    for task, lab in labels.items():
        n = lab.height
        rows, cols_, data = [], [], []
        lab_subject = lab["subject_id"].to_numpy()
        lab_t = lab["prediction_time"].dt.epoch("us").to_numpy()
        pos = np.searchsorted(subjects, lab_subject)
        if len(subjects):
            known = (pos < len(subjects)) & (
                subjects[np.minimum(pos, len(subjects) - 1)] == lab_subject
            )
        else:
            known = np.zeros(n, dtype=bool)
        li = np.flatnonzero(known)
        start, end = g_start[pos[li]], g_end[pos[li]]
        width = end - start
        # (label, group) pairs: every code group of the label's subject
        pair_label = np.repeat(li, width)
        before = np.cumsum(width) - width  # pairs of earlier labels
        pair_group = np.repeat(start - before, width) + np.arange(width.sum())
        t = lab_t[pair_label]
        base = pair_group * stride
        hi_rank = rank_at_or_before(t)
        hi = np.searchsorted(keys, base + hi_rank, side="right")
        vhi = np.searchsorted(vkeys, base + hi_rank, side="right")
        for window, delta in WINDOWS.items():
            if delta is None:
                lo_key = base - 1
            else:
                lo_key = base + rank_at_or_before(t - delta // timedelta(microseconds=1))
            lo = np.searchsorted(keys, lo_key, side="right")
            count = hi - lo
            m = count > 0
            rows.append(pair_label[m])
            cols_.append(cols[(window, "count")] + g_idx[pair_group[m]])
            data.append(count[m].astype(np.float64))
            vidx = g_vidx[pair_group]
            vlo = np.searchsorted(vkeys, lo_key, side="right")
            m = (vhi > vlo) & (vidx >= 0)
            a, b = vlo[m], vhi[m]
            r, c = pair_label[m], vidx[m]
            for agg, x in (
                ("sum", csum[b] - csum[a]),
                ("min", vmin.query(a, b) if len(a) else np.empty(0)),
                ("max", vmax.query(a, b) if len(a) else np.empty(0)),
            ):
                rows.append(r)
                cols_.append(cols[(window, agg)] + c)
                data.append(x)
        if static.height:
            s = lab.select("subject_id").with_row_index("row").join(static, on="subject_id")
            rows.append(s["row"].to_numpy())
            cols_.append(cols[("static", "present")] + s["idx"].to_numpy())
            data.append(np.ones(s.height))
            f = s.filter(pl.col("value_idx").is_not_null() & pl.col("first").is_not_null())
            rows.append(f["row"].to_numpy())
            cols_.append(cols[("static", "first")] + f["value_idx"].to_numpy())
            data.append(f["first"].to_numpy().astype(np.float64))
        out[task] = sp.csr_matrix(
            (
                np.concatenate(data).astype(np.float32),
                (np.concatenate(rows), np.concatenate(cols_)),
            ),
            shape=(n, cols["width"]),
        )
    return out


def _labels(path: Path) -> pl.DataFrame:
    return pl.read_parquet(path, columns=LABEL_COLUMNS).with_columns(
        pl.col("prediction_time").cast(pl.Datetime("us"))
    )


def _shard_job(args):
    """One shard's features for every task, written to <parts>/<task>/<split>/<n>.{npz,parquet}."""
    dataset, shard, tasks, vocab, cols, parts = args
    events = pl.read_parquet(
        dataset / "data" / shard, columns=["subject_id", "time", "code", "numeric_value"]
    )
    labels = {
        task: _labels(path / shard) for task, path in tasks.items() if (path / shard).exists()
    }
    for task, matrix in shard_features(events, labels, vocab, cols).items():
        target = parts / task / shard
        target.parent.mkdir(parents=True, exist_ok=True)
        sp.save_npz(target.with_suffix(".npz"), matrix, compressed=False)
        labels[task].write_parquet(target)
    return shard


def featurize(
    dataset: Path, tasks: dict[str, Path], out: Path, workers: int = 32, min_count: int = MIN_COUNT
) -> None:
    """Features for every task (name -> label dir) from one MEDS dataset."""
    t0 = time.time()
    vocab = vocabulary(dataset, min_count)
    cols = offsets(vocab)
    table = feature_table(vocab)
    assert table.height == cols["width"]
    out.mkdir(parents=True, exist_ok=True)
    table.write_parquet(out / "features.parquet")
    print(f"vocabulary: {vocab.height} codes, {cols['width']} columns, {time.time() - t0:.0f} s")
    shards = sorted(
        (str(p.relative_to(dataset / "data")) for p in (dataset / "data").glob("*/*.parquet")),
        key=lambda s: (s.split("/")[0], int(Path(s).stem)),
    )
    parts = out / "parts"
    jobs = [(dataset, shard, tasks, vocab, cols, parts) for shard in shards]
    # one Polars thread per worker; spawned, since forking a process whose Polars pool is running
    # can deadlock
    os.environ["POLARS_MAX_THREADS"] = "1"
    with ProcessPoolExecutor(workers, mp_context=multiprocessing.get_context("spawn")) as pool:
        list(pool.map(_shard_job, jobs))
    print(f"shards: {time.time() - t0:.0f} s", flush=True)
    # assembled one task and split at a time, in shard order
    for task in tasks:
        (out / task).mkdir(exist_ok=True)
        for split in SPLITS:
            names = [s for s in shards if s.startswith(f"{split}/") and (parts / task / s).exists()]
            if not names:
                continue
            labels = pl.concat([pl.read_parquet(parts / task / s) for s in names])
            matrix = sp.vstack(
                [sp.load_npz((parts / task / s).with_suffix(".npz")) for s in names], format="csr"
            )
            sp.save_npz(out / task / f"{split}.npz", matrix, compressed=False)
            labels.write_parquet(out / task / f"{split}_labels.parquet")
    shutil.rmtree(parts)
    (out / "done.json").write_text(
        json.dumps({"tasks": sorted(tasks), "seconds": time.time() - t0})
    )
    print(f"features: {len(tasks)} tasks, {time.time() - t0:.0f} s")


if __name__ == "__main__":
    p = argparse.ArgumentParser()
    p.add_argument("dataset", type=Path)
    p.add_argument("labels", type=Path, help="labels root (labels.py output)")
    p.add_argument("out", type=Path)
    p.add_argument("--tasks", nargs="+")
    p.add_argument("--workers", type=int, default=32)
    a = p.parse_args()
    import labels as task_labels

    names = a.tasks or list(task_labels.DEFAULT)
    featurize(a.dataset, {t.replace("/", "_"): a.labels / t for t in names}, a.out, a.workers)
