from datetime import date, timedelta

import polars as pl
from hypothesis import given
from hypothesis import strategies as st

from mimic_omop import concepts as c
from mimic_omop.etl.observation_period import (
    END_PADDING,
    PERSISTENCE_WINDOW,
    STRATEGIES,
    observation_period,
)

D0 = date(2150, 1, 1)


def day(n: int) -> date:
    return D0 + timedelta(days=n)


def events(*rows: tuple[int, int, int]) -> pl.LazyFrame:
    """(person_id, start day, end day) relative to D0."""
    return pl.LazyFrame(
        [{"person_id": p, "start": day(s), "end": day(e)} for p, s, e in rows],
        schema={"person_id": pl.Int64, "start": pl.Date, "end": pl.Date},
    )


def births(*persons: int, year: int = 2100) -> pl.LazyFrame:
    return pl.LazyFrame({"person_id": list(persons), "year_of_birth": [year] * len(persons)})


def deaths(*rows: tuple[int, int]) -> pl.LazyFrame:
    return pl.LazyFrame(
        [{"person_id": p, "death_date": day(d)} for p, d in rows],
        schema={"person_id": pl.Int64, "death_date": pl.Date},
    )


def periods(ev, birth=None, death=None, **kw) -> list[tuple]:
    birth = births(1, 2) if birth is None else birth
    death = deaths() if death is None else death
    df = observation_period(ev, birth, death, **kw).collect().sort("observation_period_id")
    assert (df["period_type_concept_id"] == c.ALGORITHM_FROM_EHR).all()
    return df.select(
        "observation_period_id", "observation_period_start_date", "observation_period_end_date"
    ).rows()


def test_themis_defaults():
    assert PERSISTENCE_WINDOW == timedelta(days=548) and END_PADDING == timedelta(days=60)
    themis = STRATEGIES["themis"]
    assert (themis.persistence_window, themis.end_padding) == (PERSISTENCE_WINDOW, END_PADDING)


def test_gaps_shorter_than_the_persistence_window_are_observed():
    assert periods(events((1, 0, 5), (1, 500, 503))) == [(1000, day(0), day(503 + 60))]


def test_a_gap_of_548_days_or_more_starts_a_new_period():
    assert periods(events((1, 0, 5), (1, 5 + 548, 5 + 548), (2, 0, 0), (2, 547, 547))) == [
        (1000, day(0), day(65)),
        (1001, day(553), day(613)),
        (2000, day(0), day(607)),
    ]


def test_end_is_capped_at_sixty_days_after_death():
    # events after death are data errors; the period still ends 60 days after death
    assert periods(events((1, 0, 100)), death=deaths((1, 10))) == [(1000, day(0), day(70))]
    # a death shortly after the last event falls inside the period
    assert periods(events((1, 0, 5)), death=deaths((1, 30))) == [(1000, day(0), day(65))]


def test_no_period_starts_before_birth():
    assert periods(events((1, -30, 5)), birth=births(1, year=2150)) == [(1000, day(0), day(65))]


def test_encounter_based_variant_merges_only_overlapping_and_adjacent_days():
    ev = events((1, 0, 5), (1, 6, 7), (1, 9, 9))
    s = STRATEGIES["encounter"]
    kw = dict(
        persistence_window=s.persistence_window,
        end_padding=s.end_padding,
        death_padding=s.death_padding,
    )
    assert periods(ev, **kw) == [(1000, day(0), day(7)), (1001, day(9), day(9))]
    # care recorded after death stays inside the period
    assert periods(ev, death=deaths((1, 6)), **kw) == [
        (1000, day(0), day(7)),
        (1001, day(9), day(9)),
    ]


spans = st.lists(
    st.tuples(st.integers(1, 3), st.integers(0, 3000), st.integers(0, 30)),
    min_size=1,
    max_size=40,
)


@given(spans)
def test_periods_cover_every_event_and_never_overlap_or_touch(rows):
    ev = events(*[(p, s, s + n) for p, s, n in rows])
    op = observation_period(ev, births(1, 2, 3), deaths()).collect()
    # each event lies in exactly one period
    inside = (
        ev.collect()
        .with_row_index("event")
        .join(op, on="person_id")
        .filter(
            pl.col("start") >= pl.col("observation_period_start_date"),
            pl.col("end") <= pl.col("observation_period_end_date"),
        )
    )
    assert inside.group_by("event").len()["len"].to_list() == [1] * len(rows)
    # periods of a person neither overlap nor are adjacent (CDM convention)
    pairs = op.join(op, on="person_id").filter(
        pl.col("observation_period_id") < pl.col("observation_period_id_right")
    )
    touching = pairs.filter(
        (pl.col("observation_period_start_date") <= pl.col("observation_period_end_date_right") + timedelta(days=1))
        & (pl.col("observation_period_start_date_right") <= pl.col("observation_period_end_date") + timedelta(days=1))
    )  # fmt: skip
    assert touching.is_empty()


def test_bounds_that_would_end_a_period_before_it_starts_are_not_applied():
    # a recorded death a year before the person's care, and a year of birth after it
    assert periods(events((1, 400, 405)), death=deaths((1, 0))) == [(1000, day(400), day(465))]
    assert periods(events((1, 0, 5)), birth=births(1, year=2160)) == [(1000, day(0), day(65))]
