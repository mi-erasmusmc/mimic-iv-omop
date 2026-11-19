"""Coded clinical events, and their routing to CDM tables by the domain of the standard concept.

Each source (registered in etl/sources.py) becomes a stream of events with the same columns,
one row per standard target of its source code (a code that maps to two concepts yields two
events). Each domain table then
selects the events of its domain, so for example a diagnosis code that maps to an Observation
concept lands in observation rather than condition_occurrence. Codes without a mapping, or
mapped outside the event domains (to a 'Meas Value' concept, say), get concept 0 and go to the
source's natural domain.

Event ids are composed from the source row so they are stable and traceable:
    prefix * 10**15 + key * 10 + k
where prefix names the source, key identifies the source row (hadm_id * 1000 + its position in
the admission, or the source's own row id such as labevent_id), and k numbers its targets.

Conventions:
- diagnoses_icd has no dates; diagnoses are dated at the start of their visit (OHDSI convention),
  although they are billing codes assigned at discharge. Prediction designs with an index date
  inside an admission must not use that admission's diagnoses as predictors.
- procedures_icd and hcpcsevents are dated by chartdate. DRGs are dated at discharge.
- Labs are assigned to their admission, or else to the visit covering their charttime (see
  visit_occurrence for the Laboratory Visits created for outpatient labs), and to the visit
  detail (care unit) covering their charttime. A numeric result written with an operator
  ('<0.5') is split into operator_concept_id and value_as_number. Text results are kept in
  value_source_value (truncated to the CDM's 50 characters); they are not mapped to concepts yet.
"""

import polars as pl

from mimic_omop import concepts as c
from mimic_omop.etl.intervals import assign

DOMAIN_TABLE = {
    "Condition": "condition_occurrence",
    "Procedure": "procedure_occurrence",
    "Observation": "observation",
    "Measurement": "measurement",
    "Drug": "drug_exposure",
    "Device": "device_exposure",
    "Specimen": "specimen",
    "Episode": "episode",
}
# domains an event can be routed to
EVENT_DOMAINS = tuple(DOMAIN_TABLE)

EVENT_SCHEMA = pl.Schema(
    {
        "event_id": pl.Int64(),
        "person_id": pl.Int64(),
        "visit_occurrence_id": pl.Int64(),
        "visit_detail_id": pl.Int64(),
        "start_date": pl.Date(),
        "start_datetime": pl.Datetime("us"),
        "type_concept_id": pl.Int64(),
        "concept_id": pl.Int64(),
        "domain_id": pl.String(),
        "source_value": pl.String(),
        "source_concept_id": pl.Int64(),
        "value_as_concept_id": pl.Int64(),
        "value_as_number": pl.Float64(),
        "operator_concept_id": pl.Int64(),
        "unit_concept_id": pl.Int64(),
        "unit_source_value": pl.String(),
        "range_low": pl.Float64(),
        "range_high": pl.Float64(),
        "value_source_value": pl.String(),
        "value_as_string": pl.String(),
        "end_date": pl.Date(),
        "end_datetime": pl.Datetime("us"),
        "verbatim_end_date": pl.Date(),
        "route_concept_id": pl.Int64(),
        "route_source_value": pl.String(),
        "quantity": pl.Float64(),
        "dose_unit_source_value": pl.String(),
        "status_concept_id": pl.Int64(),
        "status_source_value": pl.String(),
        # the record this one refers to (measurement_event_id, observation_event_id) and the
        # field concept of that record's id (meas_event_field_concept_id, ...)
        "event_link_id": pl.Int64(),
        "event_link_field_concept_id": pl.Int64(),
    }
)

PREFIX = {
    "diagnoses_icd": 1,
    "procedures_icd": 2,
    "hcpcsevents": 3,
    "drgcodes": 4,
    "labevents": 5,
    "prescriptions": 6,
    "emar": 7,
    "inputevents": 8,
    "chartevents": 9,
    "outputevents": 10,
    "omr": 11,
    "microbiologyevents": 12,
    "procedureevents": 13,
    "ed_diagnosis": 14,
    "ed_triage": 15,
    "ed_vitalsign": 16,
    "ed_medrecon": 17,
    "ed_pyxis": 18,
    "datetimeevents": 19,
}
SOURCE_VALUE_LENGTH = 50  # varchar(50) of the CDM's *_source_value fields
VALUE_STRING_LENGTH = 60  # varchar(60) of observation.value_as_string


def coded(
    events: pl.LazyFrame,
    mapping: pl.LazyFrame,
    *,
    on: list[str],
    row: list[str],
    default_domain: str | pl.Expr,
) -> pl.LazyFrame:
    """Join events to a code map: one row per target, `k` numbering the targets of each row.

    A target outside the event domains (e.g. a 'Meas Value' concept) cannot be an event's main
    concept, so such rows fall back to concept 0 in the source's natural domain. The natural
    domain can be an expression, for sources whose unmapped rows differ (numeric or text).
    """
    if isinstance(default_domain, str):
        default_domain = pl.lit(default_domain)
    usable = pl.col("target_domain_id").is_in(EVENT_DOMAINS).fill_null(False)
    return events.join(mapping, on=on, how="left").with_columns(
        concept_id=pl.when(usable).then("target_concept_id").otherwise(0),
        domain_id=pl.when(usable).then("target_domain_id").otherwise(default_domain),
        source_concept_id=pl.col("source_concept_id").fill_null(0),
        k=pl.int_range(pl.len()).over(row, order_by="target_concept_id"),
    )


def event_id(source: str, key: pl.Expr) -> pl.Expr:
    return PREFIX[source] * 10**15 + key * 10 + pl.col("k")


def admission_key(hadm_id: pl.Expr, seq: pl.Expr) -> pl.Expr:
    return hadm_id * 1000 + seq


def icd_key(hadm_id: pl.Expr, seq_num: pl.Expr, icd_version: pl.Expr) -> pl.Expr:
    """Key of a diagnoses_icd or procedures_icd row. hadm_id and seq_num alone do not identify
    one: 12 MIMIC-IV 3.1 admissions are coded in both ICD-9 and ICD-10, with the same sequence
    numbers."""
    return admission_key(hadm_id, seq_num) * 10 + (icd_version - 9)


def finish(lf: pl.LazyFrame, **columns: pl.Expr | str) -> pl.LazyFrame:
    """Select the event columns, filling the ones a source does not have with nulls."""
    unknown = set(columns) - set(EVENT_SCHEMA)
    if unknown:
        raise ValueError(f"not event columns: {sorted(unknown)}")

    def expr(name: str) -> pl.Expr:
        e = columns.get(name, pl.lit(None))
        return pl.col(e) if isinstance(e, str) else e

    return lf.select(expr(name).cast(dtype).alias(name) for name, dtype in EVENT_SCHEMA.items())


def with_visit_and_detail(
    lf: pl.LazyFrame, visits: pl.LazyFrame, details: pl.LazyFrame, time: str
) -> pl.LazyFrame:
    """`visit`: hadm_id, else the visit covering `time`; `visit_detail`: the care unit stay in
    that visit covering `time`. Expects a person_id column."""
    lf = assign(
        lf,
        visits.select(
            "person_id", "visit_occurrence_id", "visit_start_datetime", "visit_end_datetime"
        ),
        by="person_id",
        time=time,
        start="visit_start_datetime",
        end="visit_end_datetime",
        interval_id="visit_occurrence_id",
        out="covering_visit",
    ).with_columns(visit=pl.coalesce("hadm_id", "covering_visit"))
    return assign(
        lf,
        details.select(
            visit="visit_occurrence_id",
            visit_detail_id="visit_detail_id",
            start="visit_detail_start_datetime",
            end="visit_detail_end_datetime",
        ),
        by="visit",
        time=time,
        start="start",
        end="end",
        interval_id="visit_detail_id",
        out="visit_detail",
    )  # fmt: skip


# ICU rows charted this long before their stay's intime, or after its outtime, keep their stay
# (review R30): charting at handover often precedes the transfer's timestamp.
ICU_STAY_BEFORE = pl.duration(hours=6)
ICU_STAY_AFTER = pl.duration(hours=1)


def icu_stay_details(icustays: pl.LazyFrame, details: pl.LazyFrame) -> pl.LazyFrame:
    """Per ICU stay: intime, outtime, and the first and last care unit stays it spans."""
    spans = icustays.select("stay_id", "hadm_id", "intime", "outtime").join(
        details.select(
            hadm_id="visit_occurrence_id",
            detail="visit_detail_id",
            start="visit_detail_start_datetime",
            end="visit_detail_end_datetime",
        ),
        on="hadm_id",
    )
    return (
        spans.filter(pl.col("start") < pl.col("outtime"), pl.col("end") > pl.col("intime"))
        .group_by("stay_id")
        .agg(
            pl.col("intime", "outtime").first(),
            first_detail=pl.col("detail").sort_by("start").first(),
            last_detail=pl.col("detail").sort_by("start").last(),
        )
    )


def with_icu_stay_detail(lf: pl.LazyFrame, stays: pl.LazyFrame, time: str) -> pl.LazyFrame:
    """After `with_visit_and_detail`, for rows with a stay_id: a row charted up to
    ICU_STAY_BEFORE before its ICU stay gets the stay's first care unit stay, one up to
    ICU_STAY_AFTER after it the last; other rows keep the care unit stay covering their time."""
    t = pl.col(time)
    before = (t < pl.col("intime")) & (t >= pl.col("intime") - ICU_STAY_BEFORE)
    after = (t > pl.col("outtime")) & (t <= pl.col("outtime") + ICU_STAY_AFTER)
    return (
        lf.join(stays, on="stay_id", how="left")
        .with_columns(
            visit_detail=pl.when(before.fill_null(False))
            .then("first_detail")
            .when(after.fill_null(False))
            .then("last_detail")
            .otherwise("visit_detail")
        )
        .drop("intime", "outtime", "first_detail", "last_detail")
    )


def diagnoses(
    diagnoses_icd: pl.LazyFrame, visits: pl.LazyFrame, icd_map: pl.LazyFrame
) -> pl.LazyFrame:
    # left join: a diagnosis without its visit fails validation instead of disappearing
    lf = diagnoses_icd.join(
        visits.select(
            hadm_id="visit_occurrence_id",
            start_date="visit_start_date",
            start_datetime="visit_start_datetime",
        ),
        on="hadm_id",
        how="left",
    ).with_columns(
        vocabulary_id=pl.when(pl.col("icd_version") == 9)
        .then(pl.lit("ICD9CM"))
        .otherwise(pl.lit("ICD10CM")),
        source_code=pl.col("icd_code").str.strip_chars(),
    )
    lf = coded(
        lf,
        icd_map,
        on=["vocabulary_id", "source_code"],
        row=["hadm_id", "seq_num", "icd_version"],
        default_domain="Condition",
    )
    return finish(
        lf,
        event_id=event_id(
            "diagnoses_icd", icd_key(pl.col("hadm_id"), pl.col("seq_num"), pl.col("icd_version"))
        ),
        person_id="subject_id",
        visit_occurrence_id="hadm_id",
        start_date="start_date",
        start_datetime="start_datetime",
        type_concept_id=pl.lit(c.EHR_BILLING),
        concept_id="concept_id",
        domain_id="domain_id",
        source_value="source_code",
        source_concept_id="source_concept_id",
        value_as_concept_id="value_as_concept_id",
        status_concept_id=pl.when(pl.col("domain_id") == "Condition").then(
            pl.when(pl.col("seq_num") == 1)
            .then(c.PRIMARY_DIAGNOSIS)
            .otherwise(c.SECONDARY_DIAGNOSIS)
        ),
        status_source_value=pl.col("seq_num").cast(pl.String),
    )


def procedures(procedures_icd: pl.LazyFrame, icd_map: pl.LazyFrame) -> pl.LazyFrame:
    lf = procedures_icd.with_columns(
        vocabulary_id=pl.when(pl.col("icd_version") == 9)
        .then(pl.lit("ICD9Proc"))
        .otherwise(pl.lit("ICD10PCS")),
        source_code=pl.col("icd_code").str.strip_chars(),
    )
    lf = coded(
        lf,
        icd_map,
        on=["vocabulary_id", "source_code"],
        row=["hadm_id", "seq_num", "icd_version"],
        default_domain="Procedure",
    )
    return finish(
        lf,
        event_id=event_id(
            "procedures_icd", icd_key(pl.col("hadm_id"), pl.col("seq_num"), pl.col("icd_version"))
        ),
        person_id="subject_id",
        visit_occurrence_id="hadm_id",
        start_date="chartdate",
        type_concept_id=pl.lit(c.EHR_BILLING),
        concept_id="concept_id",
        domain_id="domain_id",
        source_value="source_code",
        source_concept_id="source_concept_id",
        value_as_concept_id="value_as_concept_id",
    )


def hcpcs(hcpcsevents: pl.LazyFrame, hcpcs_map: pl.LazyFrame) -> pl.LazyFrame:
    code = pl.col("hcpcs_cd").str.strip_chars()
    lf = hcpcsevents.with_columns(
        # CPT codes are five digits, or four digits and a category letter; HCPCS start with a letter
        vocabulary_id=pl.when(code.str.contains(r"^[0-9]{4}[0-9A-Z]$"))
        .then(pl.lit("CPT4"))
        .otherwise(pl.lit("HCPCS")),
        source_code=code,
    )
    lf = coded(
        lf,
        hcpcs_map,
        on=["vocabulary_id", "source_code"],
        row=["hadm_id", "seq_num"],
        default_domain="Procedure",
    )
    return finish(
        lf,
        event_id=event_id("hcpcsevents", admission_key(pl.col("hadm_id"), pl.col("seq_num"))),
        person_id="subject_id",
        visit_occurrence_id="hadm_id",
        start_date="chartdate",
        type_concept_id=pl.lit(c.EHR_BILLING),
        concept_id="concept_id",
        domain_id="domain_id",
        source_value="source_code",
        source_concept_id="source_concept_id",
        value_as_concept_id="value_as_concept_id",
    )


def drgs(drgcodes: pl.LazyFrame, visits: pl.LazyFrame, drg_map: pl.LazyFrame) -> pl.LazyFrame:
    lf = drgcodes.join(
        visits.select(
            hadm_id="visit_occurrence_id",
            start_date="visit_end_date",
            start_datetime="visit_end_datetime",
        ),
        on="hadm_id",
        how="left",
    ).with_columns(
        source_code=pl.col("description").str.strip_chars(),
        seq=pl.int_range(pl.len()).over(
            "hadm_id", order_by=["drg_type", "drg_code", "description"]
        ),
    )
    lf = coded(
        lf, drg_map, on=["source_code"], row=["hadm_id", "seq"], default_domain="Observation"
    )
    return finish(
        lf,
        event_id=event_id("drgcodes", admission_key(pl.col("hadm_id"), pl.col("seq"))),
        person_id="subject_id",
        visit_occurrence_id="hadm_id",
        start_date="start_date",
        start_datetime="start_datetime",
        type_concept_id=pl.lit(c.EHR_BILLING),
        concept_id="concept_id",
        domain_id="domain_id",
        source_value=pl.col("drg_type") + ":" + pl.col("drg_code"),
        source_concept_id="source_concept_id",
        value_as_concept_id="value_as_concept_id",
    )


OPERATOR_VALUE = r"^\s*(<=|>=|<|>|=)\s*([0-9]*\.?[0-9]+)\s*$"


def labs(
    labevents: pl.LazyFrame,
    visits: pl.LazyFrame,
    details: pl.LazyFrame,
    lab_map: pl.LazyFrame,
    units: pl.LazyFrame,
) -> pl.LazyFrame:
    lf = with_visit_and_detail(
        labevents.rename({"subject_id": "person_id"}), visits, details, "charttime"
    )
    unit = pl.col("valueuom").str.strip_chars()
    lf = lf.with_columns(
        source_code=pl.col("itemid").cast(pl.String),
        unit_source_value=pl.when(unit != "").then(unit),
        operator=pl.col("value").str.extract(OPERATOR_VALUE, 1),
        operator_number=pl.col("value").str.extract(OPERATOR_VALUE, 2).cast(pl.Float64),
    ).join(units, on="unit_source_value", how="left")
    lf = coded(lf, lab_map, on=["source_code"], row=["labevent_id"], default_domain="Measurement")
    from_text = pl.col("valuenum").is_null() & pl.col("operator").is_not_null()
    return finish(
        lf,
        event_id=event_id("labevents", pl.col("labevent_id")),
        person_id="person_id",
        visit_occurrence_id="visit",
        visit_detail_id="visit_detail",
        start_date=pl.col("charttime").dt.date(),
        start_datetime="charttime",
        type_concept_id=pl.lit(c.LAB),
        concept_id="concept_id",
        domain_id="domain_id",
        source_value="source_code",
        source_concept_id="source_concept_id",
        value_as_concept_id="value_as_concept_id",
        value_as_number=pl.when(from_text).then("operator_number").otherwise("valuenum"),
        operator_concept_id=pl.when(from_text).then(
            pl.col("operator").replace_strict(c.OPERATORS, default=None, return_dtype=pl.Int64)
        ),
        unit_concept_id="unit_concept_id",
        unit_source_value="unit_source_value",
        range_low="ref_range_lower",
        range_high="ref_range_upper",
        value_source_value=pl.col("value").str.slice(0, SOURCE_VALUE_LENGTH),
    )
