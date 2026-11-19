"""visit_occurrence from hosp.admissions, plus ED stays that did not lead to an admission.

Conventions:
- Admissions: visit_occurrence_id = hadm_id. Observation admission types are Observation Room;
  admissions through the ED (edregtime present) are ER and Inpatient; others are Inpatient.
- A visit spans the admission and all its transfers: transfers can start hours before admittime
  and end after dischtime, and visit_detail must lie inside its visit. The ED part of a visit
  comes from its ED transfers rather than edregtime, because consecutive admissions can share
  one ED registration (which transfers assigns to the first).
- Stays in hosp.transfers without an hadm_id are ED visits that did not lead to an admission.
  Contiguous rows are one stay; its visit_occurrence_id is the stay's first transfer_id
  (transfer_ids and hadm_ids come from disjoint ranges; the primary key check guards this).
- visit_end is never before visit_start.
- visit_source_value is the admission_type (the kind of visit), per the CDM spec.
- admitted_from / discharged_to use CVB, restricted to the Visit domain as the CDM requires.
  Discharge to home is 0, as the CDM prescribes ("Use concept id = 0 when a person is
  discharged to home"), although CVB maps HOME to Home Visit.
- Days with outpatient measurements (hosp.omr) or microbiology specimens recorded by date only,
  outside every admission and ED stay, get an Outpatient Visit for the whole day,
  visit_occurrence_id = 2 * 10**12 + person_id * 10**5 + days since 1970-01-01.
- Labs and microbiology specimens recorded with a time but without an hadm_id, and outside every
  other visit, get a Laboratory Visit per person and day, spanning that day's events,
  visit_occurrence_id = 10**12 + person_id * 10**5 + days since 1970-01-01.
- visit_source_value of these visits names the source tables of their events, e.g. 'labevents'
  or 'labevents+microbiologyevents'.
"""

import polars as pl

from mimic_omop import concepts as c
from mimic_omop.context import Inputs
from mimic_omop.etl.intervals import assign, islands

DISCHARGED_HOME = ("HOME",)  # CDM: concept 0 for discharge to home
# MIMIC-IV-ED dispositions as the hosp discharge locations CVB maps (ADMITTED, LEFT WITHOUT BEING
# SEEN, ELOPED and OTHER have none)
ED_ORDER = ("intime", "outtime", "stay_id")
ED_DISPOSITION = {
    "HOME": "HOME",
    "TRANSFER": "ACUTE HOSPITAL",
    "LEFT AGAINST MEDICAL ADVICE": "AGAINST ADVICE",
    "EXPIRED": "DIED",
}

# admission types that are observation stays (review R3): EU and ambulatory observation last a
# median of under a day; OBSERVATION ADMIT and DIRECT OBSERVATION, as long as other admissions,
# are inpatient stays in all but billing status
OBSERVATION_TYPES = (
    "AMBULATORY OBSERVATION",
    "EU OBSERVATION",
)


def unadmitted_stays(transfers: pl.LazyFrame) -> pl.LazyFrame:
    """transfer_id -> visit_occurrence_id for transfers without an admission (ED-only stays)."""
    return islands(
        transfers.filter(pl.col("hadm_id").is_null(), pl.col("eventtype") != "discharge"),
        by="subject_id",
        start="intime",
        end="outtime",
        order_id="transfer_id",
    ).select("transfer_id", visit_occurrence_id="island")


def unattached_events(events: pl.LazyFrame, source: str) -> pl.LazyFrame:
    """(person_id, time, source) of events with a charttime that carry no hadm_id."""
    return events.filter(pl.col("hadm_id").is_null(), pl.col("charttime").is_not_null()).select(
        person_id="subject_id", time="charttime", source=pl.lit(source)
    )


def sources_of(source: pl.Expr) -> pl.Expr:
    """visit_source_value of a visit made for events: their source tables, joined by '+'."""
    return source.unique().sort().str.join("+")


def clinic_days(days: pl.LazyFrame, visits: pl.LazyFrame) -> pl.LazyFrame:
    """Outpatient Visits for the (person_id, day) not inside any visit's dates."""
    dated = visits.select(
        "person_id",
        "visit_occurrence_id",
        start_date=pl.col("start").dt.date(),
        end_date=pl.col("end").dt.date(),
    )
    return (
        assign(
            days.group_by("person_id", "day").agg(source=sources_of(pl.col("source"))),
            dated,
            by="person_id",
            time="day",
            start="start_date",
            end="end_date",
            interval_id="visit_occurrence_id",
            out="covered_by",
        )
        .filter(pl.col("covered_by").is_null())
        .select(
            visit_occurrence_id=2 * 10**12
            + pl.col("person_id") * 10**5
            + pl.col("day").cast(pl.Int64),
            person_id="person_id",
            start=pl.col("day").cast(pl.Datetime("us")),
            end=pl.col("day").cast(pl.Datetime("us")) + pl.duration(days=1, microseconds=-1),
            visit_concept_id=pl.lit(c.OUTPATIENT, pl.Int64),
            visit_source_value="source",
        )
    )


def event_days(unattached: pl.LazyFrame, visits: pl.LazyFrame) -> pl.LazyFrame:
    """Laboratory Visits for the days with events outside every visit."""
    return (
        assign(
            unattached,
            visits,
            by="person_id",
            time="time",
            start="start",
            end="end",
            interval_id="visit_occurrence_id",
            out="covered_by",
        )
        .filter(pl.col("covered_by").is_null())
        .group_by("person_id", day=pl.col("time").dt.date())
        .agg(
            start=pl.col("time").min(),
            end=pl.col("time").max(),
            source=sources_of(pl.col("source")),
        )
        .select(
            visit_occurrence_id=10**12 + pl.col("person_id") * 10**5 + pl.col("day").cast(pl.Int64),
            person_id="person_id",
            start="start",
            end="end",
            visit_concept_id=pl.lit(c.LABORATORY_VISIT, pl.Int64),
            visit_source_value="source",
        )
    )


def visit_occurrence(
    admissions: pl.LazyFrame,
    transfers: pl.LazyFrame,
    admitted_from_map: pl.LazyFrame,
    discharged_to_map: pl.LazyFrame,
    unattached: pl.LazyFrame | None = None,
    outpatient_days: pl.LazyFrame | None = None,
    edstays: pl.LazyFrame | None = None,
) -> pl.LazyFrame:
    """unattached: (person_id, time, source) of events without hadm_id; outpatient_days:
    (person_id, day, source) of outpatient measurements and date-only events; edstays: MIMIC-IV-ED
    stays, whose arrival transport and disposition become admitted_from and discharged_to of
    the ED-only visit they are part of (the first stay's arrival, the last one's disposition)."""

    def place(lookup: pl.LazyFrame, source_column: str, name: str) -> pl.LazyFrame:
        return lookup.select(
            pl.col("source_code").alias(source_column), pl.col("target_concept_id").alias(name)
        )

    stays = transfers.filter(pl.col("eventtype") != "discharge")
    transfer_span = (
        stays.filter(pl.col("hadm_id").is_not_null())
        .group_by("hadm_id")
        .agg(t_start=pl.col("intime").min(), t_end=pl.col("outtime").max())
    )
    admitted = (
        admissions.join(transfer_span, on="hadm_id", how="left")
        .join(
            place(admitted_from_map, "admission_location", "admitted_from_concept_id"),
            on="admission_location",
            how="left",
        )
        .join(
            place(
                discharged_to_map.filter(~pl.col("source_code").is_in(DISCHARGED_HOME)),
                "discharge_location",
                "discharged_to_concept_id",
            ),
            on="discharge_location",
            how="left",
        )
        .select(
            "admitted_from_concept_id",
            "discharged_to_concept_id",
            visit_occurrence_id="hadm_id",
            person_id="subject_id",
            visit_concept_id=pl.when(pl.col("admission_type").is_in(OBSERVATION_TYPES))
            .then(c.OBSERVATION_ROOM)
            .when(pl.col("edregtime").is_not_null())
            .then(c.ER_AND_INPATIENT)
            .otherwise(c.INPATIENT),
            # transfers say which admission an ED stay belongs to; two admissions can share one
            # ED registration, so edregtime only stands in when there are no transfers
            start=pl.min_horizontal("admittime", pl.coalesce("t_start", "edregtime")),
            end=pl.max_horizontal("dischtime", "t_end"),
            visit_source_value="admission_type",
            admitted_from_source_value="admission_location",
            discharged_to_source_value="discharge_location",
        )
    )
    unadmitted = (
        stays.join(unadmitted_stays(transfers), on="transfer_id")
        .group_by("visit_occurrence_id")
        .agg(
            person_id=pl.col("subject_id").first(),
            start=pl.col("intime").min(),
            end=pl.col("outtime").max(),
        )
        .with_columns(
            visit_concept_id=pl.lit(c.EMERGENCY_ROOM, pl.Int64),
            visit_source_value=pl.lit("ED"),
        )
    )
    if edstays is not None:
        ed = (
            edstays.join(
                stays.select("transfer_id", "subject_id", "intime"), on=["subject_id", "intime"]
            )
            .join(unadmitted_stays(transfers), on="transfer_id")
            .group_by("visit_occurrence_id")
            .agg(
                # stays can share an intime: outtime and stay_id make the order deterministic
                admitted_from_source_value=pl.col("arrival_transport").sort_by(*ED_ORDER).first(),
                discharged_to_source_value=pl.col("disposition").sort_by(*ED_ORDER).last(),
            )
            .with_columns(
                admitted_from_concept_id=pl.col("admitted_from_source_value").replace_strict(
                    c.ARRIVAL, default=None, return_dtype=pl.Int64
                ),
                discharge_location=pl.col("discharged_to_source_value").replace_strict(
                    ED_DISPOSITION, default=None
                ),
            )
            .join(
                place(
                    discharged_to_map.filter(~pl.col("source_code").is_in(DISCHARGED_HOME)),
                    "discharge_location",
                    "discharged_to_concept_id",
                ),
                on="discharge_location",
                how="left",
            )
            .drop("discharge_location")
        )
        unadmitted = unadmitted.join(ed, on="visit_occurrence_id", how="left")
    stays_and_admissions = pl.concat([admitted, unadmitted], how="diagonal_relaxed").with_columns(
        end=pl.max_horizontal("end", "start")
    )
    parts = [stays_and_admissions]
    if outpatient_days is not None:
        parts.append(clinic_days(outpatient_days, stays_and_admissions))
    if unattached is not None:
        parts.append(event_days(unattached, pl.concat(parts, how="diagonal_relaxed")))
    return pl.concat(parts, how="diagonal_relaxed").select(
        "visit_occurrence_id",
        "person_id",
        "visit_concept_id",
        visit_start_date=pl.col("start").dt.date(),
        visit_start_datetime="start",
        visit_end_date=pl.col("end").dt.date(),
        visit_end_datetime="end",
        visit_type_concept_id=pl.lit(c.EHR),
        visit_source_value="visit_source_value",
        visit_source_concept_id=pl.lit(0),
        admitted_from_concept_id=pl.col("admitted_from_concept_id").fill_null(0),
        admitted_from_source_value="admitted_from_source_value",
        discharged_to_concept_id=pl.col("discharged_to_concept_id").fill_null(0),
        discharged_to_source_value="discharged_to_source_value",
        preceding_visit_occurrence_id=pl.col("visit_occurrence_id")
        .shift(1)
        .over("person_id", order_by=["start", "visit_occurrence_id"]),
    )


def build(inp: Inputs) -> pl.LazyFrame:
    micro = inp.src.scan("hosp/microbiologyevents")
    return visit_occurrence(
        inp.src.scan("hosp/admissions"),
        inp.src.scan("hosp/transfers"),
        inp.vocab.lookup("mimiciv_hosp.admissions.admission_location", domains=("Visit",)),
        inp.vocab.lookup("mimiciv_hosp.admissions.discharge_location", domains=("Visit",)),
        pl.concat(
            [
                unattached_events(inp.src.scan("hosp/labevents"), "labevents"),
                unattached_events(micro, "microbiologyevents"),
            ]
        ),
        pl.concat(
            [
                inp.src.scan("hosp/omr").select(
                    person_id="subject_id", day="chartdate", source=pl.lit("omr")
                ),
                micro.filter(pl.col("charttime").is_null()).select(
                    person_id="subject_id", day="chartdate", source=pl.lit("microbiologyevents")
                ),
            ]
        ),
        inp.src.scan("ed/edstays") if inp.src.has("ed/edstays") else None,
    )
