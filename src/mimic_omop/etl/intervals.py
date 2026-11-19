"""Interval logic as window expressions: merging overlapping intervals, and assigning points in
time to the interval that covers them. Both avoid as-of and range joins, which the Polars
streaming engine does not execute natively."""

from datetime import timedelta

import polars as pl


def islands(
    lf: pl.LazyFrame,
    *,
    by: str | list[str],
    start: str,
    end: str,
    order_id: str,
    gap: timedelta | None = None,
) -> pl.LazyFrame:
    """Add an `island` column: intervals of the same `by` key that overlap or touch, directly or
    through a chain of others, get the `order_id` of the earliest interval of their island.

    With `gap`, intervals that start at most `gap` after the island's latest end also join it.
    """
    order = [start, order_id]
    reach = pl.col(end).cum_max().shift(1).over(by, order_by=order)
    if gap is not None:
        reach = reach + gap
    new_island = reach.is_null() | (pl.col(start) > reach)
    return lf.with_columns(
        island=pl.when(new_island).then(pl.col(order_id)).forward_fill().over(by, order_by=order)
    )


def assign(
    events: pl.LazyFrame,
    intervals: pl.LazyFrame,
    *,
    by: str | list[str],
    time: str,
    start: str,
    end: str,
    interval_id: str,
    out: str,
) -> pl.LazyFrame:
    """Add column `out` to events: the `interval_id` of the latest-starting interval with the same
    `by` key that covers the event's `time` (start <= time <= end), or null if none does; of
    intervals starting at the same time, the one with the highest id.

    Interval starts and events are merged into one time line per key, and the latest interval
    start is carried forward to each event. With nested intervals, an event after the inner one
    ended but inside the outer one stays unassigned; MIMIC visits and stays do not nest.
    """
    marks = intervals.select(
        pl.col(by),
        pl.col(start).alias("_t"),
        _kind=pl.lit(0, pl.Int8),
        _id=pl.col(interval_id),
        _end=pl.col(end),
    )
    points = events.with_columns(pl.col(time).alias("_t"), _kind=pl.lit(1, pl.Int8))
    # an interval starting at an event's time covers it; ties between intervals go to the
    # higher id, so the result never depends on row order
    order = ["_t", "_kind", "_id"]
    return (
        pl.concat([marks, points], how="diagonal_relaxed")
        .with_columns(
            _cur=pl.col("_id").forward_fill().over(by, order_by=order),
            _cur_end=pl.col("_end").forward_fill().over(by, order_by=order),
        )
        .filter(pl.col("_kind") == 1)
        .with_columns(pl.when(pl.col("_t") <= pl.col("_cur_end")).then("_cur").alias(out))
        .drop("_t", "_kind", "_id", "_end", "_cur", "_cur_end")
    )
