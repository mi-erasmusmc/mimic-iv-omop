"""observation_period, following the THEMIS convention for EHR data
(https://ohdsi.github.io/Themis/obs_periods_for_ehr.html):

- A period starts at a person's first clinical event, but not before birth (January 1 of the
  year of birth, the only birth information MIMIC has).
- A gap of PERSISTENCE_WINDOW (548 days) or more between clinical events ends the period; the next
  one starts at the next event.
- A period ends END_PADDING (60 days) after its last clinical event, or 60 days after death if
  that is earlier.
- A bound that would end a period before it starts is a source error and is not applied: a
  recorded death before the period's first event (19 persons in MIMIC-IV 3.1, all from `dod`), or
  a year of birth after all of the period's events.
- Death alone does not create a period.

Clinical events are visits and the records of every event table built so far; their start and
end dates both count. A person without any clinical event has no period and is excluded from the
CDM (see person.py).

The gap between two events is the next start minus the latest end so far, in days. The
"encounter" strategy (`run --observation-periods encounter`) uses the same logic with a 2-day
persistence window, no end padding and no death bound: periods are the days with recorded care,
adjacent days merged as the CDM requires, including care recorded after a death (transfers out
of a unit after the death, for example). It is the alternative a reviewer may prefer for an
inpatient-only source like MIMIC; see docs/etl/observation_period.md.

observation_period_id = person_id * 1000 + the period's number for that person, from 0.
"""

from dataclasses import dataclass
from datetime import timedelta

import polars as pl

from mimic_omop import concepts as c
from mimic_omop.context import Inputs
from mimic_omop.etl.death import death
from mimic_omop.etl.intervals import islands
from mimic_omop.etl.person import year_of_birth

PERSISTENCE_WINDOW = timedelta(days=548)
END_PADDING = timedelta(days=60)


@dataclass(frozen=True)
class Strategy:
    persistence_window: timedelta
    end_padding: timedelta
    death_padding: timedelta | None  # None: death does not bound a period
    description: str


STRATEGIES = {
    "themis": Strategy(
        PERSISTENCE_WINDOW,
        END_PADDING,
        END_PADDING,
        "THEMIS EHR: persistence window 548 days, end 60 days after last event",
    ),
    "encounter": Strategy(
        timedelta(days=2),
        timedelta(0),
        None,
        "encounter-based: days with recorded care, adjacent days merged, no end padding",
    ),
}

# CDM tables whose records are clinical events: (start date, end date or None)
EVENT_DATES = {
    "visit_occurrence": ("visit_start_date", "visit_end_date"),
    "condition_occurrence": ("condition_start_date", "condition_end_date"),
    "procedure_occurrence": ("procedure_date", "procedure_end_date"),
    "measurement": ("measurement_date", None),
    "observation": ("observation_date", None),
    "drug_exposure": ("drug_exposure_start_date", "drug_exposure_end_date"),
    "device_exposure": ("device_exposure_start_date", "device_exposure_end_date"),
}


def event_dates(tables: dict[str, pl.LazyFrame]) -> pl.LazyFrame:
    """(person_id, start, end) of every clinical event, one row per distinct span."""
    parts = []
    for table, lf in tables.items():
        start, end = EVENT_DATES[table]
        parts.append(
            lf.select(
                "person_id",
                start=pl.col(start),
                end=pl.coalesce(pl.col(end), pl.col(start)) if end else pl.col(start),
            )
        )
    return pl.concat(parts).filter(pl.col("start").is_not_null()).unique()


def observation_period(
    events: pl.LazyFrame,
    births: pl.LazyFrame,
    deaths: pl.LazyFrame,
    persistence_window: timedelta = PERSISTENCE_WINDOW,
    end_padding: timedelta = END_PADDING,
    death_padding: timedelta | None = END_PADDING,
) -> pl.LazyFrame:
    """events: (person_id, start, end) dates; births: (person_id, year_of_birth);
    deaths: (person_id, death_date). A period ends end_padding after its last event, or
    death_padding after death if that is earlier; death_padding None: death is not a bound."""
    if death_padding is None:
        deaths = deaths.filter(False)
    spans = (
        islands(
            events.with_columns(
                end=pl.max_horizontal("start", "end"),
                order=pl.int_range(pl.len()).over("person_id", order_by=["start", "end"]),
            ),
            by="person_id",
            start="start",
            end="end",
            order_id="order",
            # a new period starts after a gap of persistence_window or more
            gap=persistence_window - timedelta(days=1),
        )
        .group_by("person_id", "island")
        .agg(first=pl.col("start").min(), last=pl.col("end").max())
    )
    return (
        spans.join(births, on="person_id", how="left")
        .join(deaths, on="person_id", how="left")
        .with_columns(
            birth=pl.date(pl.col("year_of_birth"), 1, 1),
            after_death=pl.col("death_date") + (death_padding or timedelta(0)),
            end=pl.col("last") + end_padding,
        )
        .with_columns(
            # bounds that would end a period before it starts are source errors (a recorded death
            # before the person's care, care before the year of birth) and are not applied, so
            # every person with clinical events keeps a period and their records
            end=pl.when(pl.col("after_death") >= pl.col("first"))
            .then(pl.min_horizontal("end", "after_death"))
            .otherwise("end"),
        )
        .with_columns(
            start=pl.when(pl.col("birth") <= pl.col("end"))
            .then(pl.max_horizontal("first", "birth"))
            .otherwise("first")
        )
        .select(
            observation_period_id=pl.col("person_id") * 1000
            + pl.int_range(pl.len()).over("person_id", order_by="start"),
            person_id="person_id",
            observation_period_start_date="start",
            observation_period_end_date="end",
            period_type_concept_id=pl.lit(c.ALGORITHM_FROM_EHR),
        )
    )


def build(inp: Inputs) -> pl.LazyFrame:
    patients = inp.src.scan("hosp/patients")
    tables = {t: inp.cdm.scan(t) for t in EVENT_DATES if inp.cdm.has(t)}
    strategy = STRATEGIES[inp.release.observation_periods]
    return observation_period(
        event_dates(tables),
        patients.select(person_id="subject_id", year_of_birth=year_of_birth()),
        death(patients, inp.src.scan("hosp/admissions")).select("person_id", "death_date"),
        strategy.persistence_window,
        strategy.end_padding,
        strategy.death_padding,
    )
