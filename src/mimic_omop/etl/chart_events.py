"""ICU charted data: icu.chartevents (vital signs, assessments, nursing documentation),
icu.outputevents (fluid outputs), icu.procedureevents (ventilation, dialysis, lines, imaging,
with start and end times) and icu.datetimeevents (dates of line insertions, line care, wounds), as
coded events.

Conventions:
- Item concepts come from CVB's ICU item mappings (mimiciv_icu.d_items.label and
  mimiciv_icu.chartevents.itemid, both keyed by itemid); records follow their concept's domain.
  Unmapped chart items go to measurement when they have a numeric value and to observation when
  they only have text.
- Numeric results become value_as_number with the unit from valueuom (CVB, else UCUM); text
  results are kept in value_source_value and, for observations, value_as_string. CVB maps a few
  text values (heart rhythms) to value concepts.
- Records are assigned to their admission and to the care unit stay covering charttime; rows
  charted shortly before or after their ICU stay keep that stay (review R30).
- Event keys rank the rows within their ICU stay (MIMIC gives these tables no row id).
"""

import polars as pl

from mimic_omop import concepts as c
from mimic_omop.etl.events import (
    SOURCE_VALUE_LENGTH,
    VALUE_STRING_LENGTH,
    coded,
    event_id,
    finish,
    icu_stay_details,
    with_icu_stay_detail,
    with_visit_and_detail,
)

STAY_KEY = 10**6  # rows per ICU stay; a collision would fail the primary key check


def charted(
    chartevents: pl.LazyFrame,
    visits: pl.LazyFrame,
    details: pl.LazyFrame,
    icustays: pl.LazyFrame,
    item_map: pl.LazyFrame,
    value_map: pl.LazyFrame,
    units: pl.LazyFrame,
) -> pl.LazyFrame:
    unit = pl.col("valueuom").str.strip_chars()
    text = pl.col("value").str.strip_chars()
    lf = (
        chartevents.rename({"subject_id": "person_id"})
        .with_columns(
            source_code=pl.col("itemid").cast(pl.String),
            unit_source_value=pl.when(unit != "").then(unit),
            value_code=text,
            seq=pl.int_range(pl.len()).over(
                "stay_id", order_by=["charttime", "itemid", "storetime", "value"]
            ),
        )
        .join(units, on="unit_source_value", how="left")
        .join(
            value_map.select(value_code="source_code", value_concept="target_concept_id"),
            on="value_code",
            how="left",
        )
    )
    lf = coded(
        lf,
        item_map,
        on=["source_code"],
        row=["stay_id", "seq"],
        default_domain=pl.when(pl.col("valuenum").is_not_null())
        .then(pl.lit("Measurement"))
        .otherwise(pl.lit("Observation")),
    )
    lf = with_visit_and_detail(lf, visits, details, "charttime")
    lf = with_icu_stay_detail(lf, icu_stay_details(icustays, details), "charttime")
    numeric = pl.col("valuenum").is_not_null()
    return finish(
        lf,
        event_id=event_id("chartevents", pl.col("stay_id") * STAY_KEY + pl.col("seq")),
        person_id="person_id",
        visit_occurrence_id="visit",
        visit_detail_id="visit_detail",
        start_date=pl.col("charttime").dt.date(),
        start_datetime="charttime",
        type_concept_id=pl.lit(c.EHR),
        concept_id="concept_id",
        domain_id="domain_id",
        source_value="source_code",
        source_concept_id="source_concept_id",
        value_as_number="valuenum",
        value_as_concept_id=pl.when(~numeric).then("value_concept"),
        unit_concept_id=pl.when(numeric).then("unit_concept_id"),
        unit_source_value="unit_source_value",
        value_source_value=pl.col("value").str.slice(0, SOURCE_VALUE_LENGTH),
        value_as_string=pl.when(~numeric).then(pl.col("value").str.slice(0, VALUE_STRING_LENGTH)),
    )


def outputs(
    outputevents: pl.LazyFrame,
    visits: pl.LazyFrame,
    details: pl.LazyFrame,
    icustays: pl.LazyFrame,
    item_map: pl.LazyFrame,
    units: pl.LazyFrame,
) -> pl.LazyFrame:
    unit = pl.col("valueuom").str.strip_chars()
    lf = (
        outputevents.rename({"subject_id": "person_id"})
        .with_columns(
            source_code=pl.col("itemid").cast(pl.String),
            unit_source_value=pl.when(unit != "").then(unit),
            seq=pl.int_range(pl.len()).over(
                "stay_id", order_by=["charttime", "itemid", "storetime"]
            ),
        )
        .join(units, on="unit_source_value", how="left")
    )
    lf = coded(
        lf, item_map, on=["source_code"], row=["stay_id", "seq"], default_domain="Measurement"
    )
    lf = with_visit_and_detail(lf, visits, details, "charttime")
    lf = with_icu_stay_detail(lf, icu_stay_details(icustays, details), "charttime")
    return finish(
        lf,
        event_id=event_id("outputevents", pl.col("stay_id") * STAY_KEY + pl.col("seq")),
        person_id="person_id",
        visit_occurrence_id="visit",
        visit_detail_id="visit_detail",
        start_date=pl.col("charttime").dt.date(),
        start_datetime="charttime",
        type_concept_id=pl.lit(c.EHR),
        concept_id="concept_id",
        domain_id="domain_id",
        source_value="source_code",
        source_concept_id="source_concept_id",
        value_as_number="value",
        unit_concept_id="unit_concept_id",
        unit_source_value="unit_source_value",
    )


def procedures(
    procedureevents: pl.LazyFrame,
    visits: pl.LazyFrame,
    details: pl.LazyFrame,
    icustays: pl.LazyFrame,
    item_map: pl.LazyFrame,
) -> pl.LazyFrame:
    """ICU procedures with their start and end. `value` holds the duration or a count, which the
    times already give, so it is not kept; neither is `location` (line site), which the CDM
    procedure has no field for."""
    lf = procedureevents.rename({"subject_id": "person_id"}).with_columns(
        source_code=pl.col("itemid").cast(pl.String),
        seq=pl.int_range(pl.len()).over(
            "stay_id", order_by=["starttime", "itemid", "orderid", "endtime"]
        ),
    )
    lf = coded(lf, item_map, on=["source_code"], row=["stay_id", "seq"], default_domain="Procedure")
    lf = with_visit_and_detail(lf, visits, details, "starttime")
    lf = with_icu_stay_detail(lf, icu_stay_details(icustays, details), "starttime")
    end = pl.max_horizontal("endtime", "starttime")
    return finish(
        lf,
        event_id=event_id("procedureevents", pl.col("stay_id") * 10**5 + pl.col("seq")),
        person_id="person_id",
        visit_occurrence_id="visit",
        visit_detail_id="visit_detail",
        start_date=pl.col("starttime").dt.date(),
        start_datetime="starttime",
        end_date=end.dt.date(),
        end_datetime=end,
        type_concept_id=pl.lit(c.EHR),
        concept_id="concept_id",
        domain_id="domain_id",
        source_value="source_code",
        source_concept_id="source_concept_id",
    )


def dated(
    datetimeevents: pl.LazyFrame,
    visits: pl.LazyFrame,
    details: pl.LazyFrame,
    item_map: pl.LazyFrame,
) -> pl.LazyFrame:
    """ICU dates: a row documents that something happened at `value` (a line was inserted, a
    dressing changed, a pressure ulcer identified), and nurses chart the same date again every
    shift, and again in later ICU stays while the line stays in. So each (patient, item, value)
    becomes one record, dated at `value`, in the ICU stay and admission where it was first
    charted; a date cannot be later than its first recording (storetime), which catches
    mistyped future dates. Items with a date but no time keep the date only (no datetime): at
    midnight they would precede what happened later that day, which a model predicting within
    the day would see (evaluation E3a); their care unit stay is the one covering the first
    charting when that is on the same day."""
    value = pl.col("value").str.to_datetime("%Y-%m-%d %H:%M:%S")
    same_day = pl.col("time").dt.date() == pl.col("charttime").dt.date()
    lf = (
        datetimeevents.group_by("subject_id", "itemid", "value")
        .agg(
            pl.col("stay_id", "hadm_id").sort_by("charttime", "stay_id").first(),
            date_only=(pl.col("valueuom") == "Date").first(),
            charttime=pl.col("charttime").min(),
            storetime=pl.col("storetime").min(),
        )
        .rename({"subject_id": "person_id"})
        .with_columns(
            source_code=pl.col("itemid").cast(pl.String),
            time=pl.min_horizontal(value, "storetime"),
            seq=pl.int_range(pl.len()).over("stay_id", order_by=["itemid", "value"]),
        )
        .with_columns(
            at=pl.when(pl.col("date_only") & same_day).then("charttime").otherwise("time")
        )
    )
    lf = coded(lf, item_map, on=["source_code"], row=["stay_id", "seq"], default_domain="Procedure")
    lf = with_visit_and_detail(lf, visits, details, "at")
    return finish(
        lf,
        event_id=event_id("datetimeevents", pl.col("stay_id") * STAY_KEY + pl.col("seq")),
        person_id="person_id",
        visit_occurrence_id="visit",
        visit_detail_id="visit_detail",
        start_date=pl.col("time").dt.date(),
        start_datetime=pl.when(~pl.col("date_only")).then("time"),
        type_concept_id=pl.lit(c.EHR),
        concept_id="concept_id",
        domain_id="domain_id",
        source_value="source_code",
        source_concept_id="source_concept_id",
        value_source_value="value",
    )
