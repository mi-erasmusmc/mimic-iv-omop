import polars as pl
from hypothesis import given
from hypothesis import strategies as st

from mimic_omop.etl.intervals import islands

intervals = st.lists(
    st.tuples(st.integers(1, 3), st.integers(0, 50), st.integers(0, 10)),  # key, start, length
    min_size=1,
    max_size=40,
)


def assign(rows) -> pl.DataFrame:
    lf = pl.LazyFrame(
        [{"key": k, "id": i, "start": s, "end": s + n} for i, (k, s, n) in enumerate(rows)]
    )
    return islands(lf, by="key", start="start", end="end", order_id="id").collect()


def test_chains_merge_and_gaps_split():
    df = assign([(1, 0, 5), (1, 3, 1), (1, 5, 2), (1, 8, 1), (2, 0, 1)])
    assert df.sort("id")["island"].to_list() == [0, 0, 0, 3, 4]


@given(intervals)
def test_islands_are_maximal_merged_runs(rows):
    df = assign(rows)
    spans = df.group_by("key", "island").agg(
        lo=pl.col("start").min(), hi=pl.col("end").max(), first=pl.col("id").sort_by("start", "id").first()
    )  # fmt: skip
    # each island is named after its earliest interval
    assert (spans["island"] == spans["first"]).all()
    # islands of the same key are disjoint and do not touch
    pairs = spans.join(spans, on="key").filter(pl.col("island") != pl.col("island_right"))
    assert pairs.filter(
        (pl.col("lo") <= pl.col("hi_right")) & (pl.col("lo_right") <= pl.col("hi"))
    ).is_empty()
    # within an island, the intervals cover it without a gap
    for (_, _), g in df.group_by("key", "island"):
        g = g.sort("start")
        assert (g["start"].slice(1) <= g["end"].cum_max().slice(0, g.height - 1)).all()


def test_assign_picks_the_covering_interval():
    from mimic_omop.etl.intervals import assign

    intervals = pl.LazyFrame(
        {"key": [1, 1, 2], "iid": [10, 11, 20], "s": [0, 10, 0], "e": [5, 12, 3]}
    )
    events = pl.LazyFrame({"key": [1, 1, 1, 1, 2, 3], "t": [0, 5, 7, 10, 4, 1], "eid": range(6)})
    out = assign(
        events, intervals, by="key", time="t", start="s", end="e", interval_id="iid", out="v"
    )
    assert out.collect().sort("eid")["v"].to_list() == [10, 10, None, 11, None, None]


disjoint = st.lists(
    st.tuples(st.integers(1, 3), st.integers(1, 5), st.integers(0, 4)),  # key, gap before, length
    max_size=15,
)


@given(
    disjoint, st.lists(st.tuples(st.integers(1, 3), st.integers(0, 100)), min_size=1, max_size=40)
)
def test_assign_matches_brute_force(spans, points):
    from mimic_omop.etl.intervals import assign

    rows, ends = [], {}
    for i, (key, gap, length) in enumerate(spans):  # disjoint intervals per key, like visits
        start = ends.get(key, -1) + gap
        rows.append({"key": key, "iid": i, "s": start, "e": start + length})
        ends[key] = start + length
    intervals = pl.LazyFrame(
        rows, schema={"key": pl.Int64, "iid": pl.Int64, "s": pl.Int64, "e": pl.Int64}
    )
    events = pl.LazyFrame([{"key": k, "t": t, "eid": i} for i, (k, t) in enumerate(points)])
    got = assign(
        events, intervals, by="key", time="t", start="s", end="e", interval_id="iid", out="v"
    )
    expected = [
        next((r["iid"] for r in rows if r["key"] == k and r["s"] <= t <= r["e"]), None)
        for k, t in points
    ]
    assert got.collect().sort("eid")["v"].to_list() == expected


def test_assign_breaks_ties_between_intervals_by_id():
    from mimic_omop.etl.intervals import assign

    intervals = pl.LazyFrame({"key": [1, 1], "iid": [7, 3], "s": [0, 0], "e": [10, 2]})
    events = pl.LazyFrame({"key": [1, 1], "t": [1, 5], "eid": [0, 1]})
    out = assign(
        events, intervals, by="key", time="t", start="s", end="e", interval_id="iid", out="v"
    )
    assert out.collect().sort("eid")["v"].to_list() == [7, 7]
