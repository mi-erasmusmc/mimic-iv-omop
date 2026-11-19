import sys
from datetime import datetime, timedelta
from pathlib import Path

import numpy as np
import polars as pl
import pytest

sys.path.insert(0, str(Path(__file__).parents[1]))
import featurize  # noqa: E402


def t(s: str) -> datetime:
    return datetime.fromisoformat(s)


def write(root: Path, split: str, shard: int, df: pl.DataFrame) -> None:
    (root / split).mkdir(parents=True, exist_ok=True)
    df.write_parquet(root / split / f"{shard}.parquet")


def events_frame(rows) -> pl.DataFrame:
    return pl.DataFrame(
        rows,
        schema={"subject_id": pl.Int64, "time": pl.Datetime("us"), "code": pl.String,
                "numeric_value": pl.Float32},
        orient="row",
    )  # fmt: skip


def label_frame(rows) -> pl.DataFrame:
    return pl.DataFrame(
        rows,
        schema={"subject_id": pl.Int64, "prediction_time": pl.Datetime("us"),
                "boolean_value": pl.Boolean},
        orient="row",
    )  # fmt: skip


def run(tmp_path: Path, events: dict, labels: dict, min_count: int = 1):
    data, lab = tmp_path / "meds", tmp_path / "labels"
    for (split, shard), rows in events.items():
        write(data / "data", split, shard, events_frame(rows))
    for (split, shard), rows in labels.items():
        write(lab, split, shard, label_frame(rows))
    featurize.featurize(data, {"task": lab}, tmp_path / "out", workers=2, min_count=min_count)
    names = pl.read_parquet(tmp_path / "out/features.parquet")["name"].to_list()
    return tmp_path / "out", names


def row(out: Path, split: str, names: list[str], i: int) -> dict[str, float]:
    import scipy.sparse as sp

    m = sp.load_npz(out / "task" / f"{split}.npz").tocsr()
    r = m[i]
    return {names[j]: float(v) for j, v in zip(r.indices, r.data, strict=True)}


def test_windows_end_at_the_prediction_time_and_count_every_event(tmp_path):
    events = {
        ("train", 0): [
            # sodium twice at 08:00 and once at 09:00, heart rate once at 08:00, all on 1 January
            (1, t("2150-01-01 08:00"), "NA", 140.0),
            (1, t("2150-01-01 08:00"), "NA", 135.0),
            (1, t("2150-01-01 08:00"), "HR", 80.0),
            (1, t("2150-01-01 09:00"), "NA", 130.0),
            # a sodium five days earlier, and one after the prediction time
            (1, t("2149-12-27 10:00"), "NA", 150.0),
            (1, t("2150-01-01 13:00"), "NA", 120.0),
            # a static code
            (1, None, "SEX//F", None),
        ]
    }
    labels = {("train", 0): [(1, t("2150-01-01 12:00"), True)]}
    out, names = run(tmp_path, events, labels)
    f = row(out, "train", names, 0)
    # MEDS-Tab 0.2 gives NA count 1 and sum 275 here (the last event of each time dropped)
    assert f["NA/count/1d"] == 3 and f["NA/sum/1d"] == 405 and f["NA/min/1d"] == 130
    assert f["NA/max/1d"] == 140 and f["HR/count/1d"] == 1 and f["HR/sum/1d"] == 80
    assert f["NA/count/7d"] == 4 and f["NA/max/7d"] == 150 and f["NA/count/full"] == 4
    assert "NA/count/30d" in f and f["SEX//F/present/static"] == 1


def test_a_window_without_events_is_empty_and_the_end_is_inclusive(tmp_path):
    events = {("train", 0): [(1, t("2150-01-01 08:00"), "NA", 140.0),
                             (1, t("2150-01-05 08:00"), "NA", 130.0)]}  # fmt: skip
    labels = {
        ("train", 0): [
            (1, t("2150-01-03 08:00"), False),  # 1d window empty (MEDS-Tab: the 1 Jan window)
            (1, t("2150-01-05 08:00"), True),  # an event exactly at t is in the window
            (1, t("2149-12-31 08:00"), False),  # before any event
        ]
    }
    out, names = run(tmp_path, events, labels)
    assert "NA/count/1d" not in row(out, "train", names, 0)
    assert row(out, "train", names, 0)["NA/count/full"] == 1
    assert row(out, "train", names, 1)["NA/count/1d"] == 1
    assert row(out, "train", names, 2) == {}


@pytest.mark.parametrize("seed", range(5))
def test_random_data_matches_a_direct_computation(tmp_path, seed):
    rng = np.random.default_rng(seed)
    start = t("2150-01-01 00:00")
    codes = ["A", "B", "C", "D"]

    def stamp():  # coarse times, so that many events share a time
        return start + timedelta(hours=int(rng.integers(0, 24 * 60)))

    events, labels = {}, {}
    for shard in (0, 1):
        ev = []
        for s in range(shard * 10, shard * 10 + 10):
            for _ in range(int(rng.integers(0, 60))):
                v = None if rng.random() < 0.3 else float(rng.normal(0, 10))
                ev.append((s, stamp(), codes[int(rng.integers(0, 4))], v))
        events[("train", shard)] = ev
        # a shard's labels are its own subjects', and one subject without events
        labels[("train", shard)] = [
            (int(rng.integers(shard * 10, shard * 10 + 10)), stamp(), bool(rng.integers(0, 2)))
            for _ in range(15)
        ] + [(99, stamp(), False)]
    out, names = run(tmp_path, events, labels)
    ev = pl.concat(events_frame(r) for r in events.values()).with_columns(
        pl.col("numeric_value").cast(pl.Float64)
    )
    labs = [lab for shard in (0, 1) for lab in labels[("train", shard)]]
    for i, (s, pt, _) in enumerate(labs):
        got = row(out, "train", names, i)
        want = {}
        for window, delta in featurize.WINDOWS.items():
            w = ev.filter(pl.col("subject_id") == s, pl.col("time") <= pt)
            if delta is not None:
                w = w.filter(pl.col("time") > pt - delta)
            for code, g in w.group_by("code"):
                want[f"{code[0]}/count/{window}"] = g.height
                vals = g["numeric_value"].drop_nulls()
                if len(vals):
                    want[f"{code[0]}/sum/{window}"] = vals.sum()
                    want[f"{code[0]}/min/{window}"] = vals.min()
                    want[f"{code[0]}/max/{window}"] = vals.max()
        assert set(got) == set(want), (i, set(got) ^ set(want))
        for k in want:
            assert got[k] == pytest.approx(want[k], rel=1e-5, abs=1e-3), (i, k)
