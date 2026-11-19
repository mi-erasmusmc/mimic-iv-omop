"""Microbiology: hosp.microbiologyevents as specimens, test results and susceptibilities.

MIMIC has one row per specimen and test (micro_specimen_id, test_seq), repeated for each organism
isolated (org_itemid, isolate_num) and, for an isolate tested for antibiotics, once per
antibiotic (ab_itemid). A row is a test result when it has no antibiotic, and a susceptibility
otherwise; an isolate tested for antibiotics has no row of its own.

Conventions (no OHDSI convention for microbiology is settled; see docs/etl/specimen.md):
- Each specimen is a SPECIMEN record, linked to its test results by FACT_RELATIONSHIP.
- A test result is an event of the test's concept (a culture or assay), with the organism found
  as value_as_concept_id, or, without an organism, the comment ("NO GROWTH.") as
  value_source_value. An isolate known only from its susceptibility rows gets the same record,
  derived from its first susceptibility row (k = ISOLATE_K + target).
- A susceptibility is a Measurement of the antibiotic's susceptibility concept, with S/I/R as
  value_as_concept_id, the MIC as value_as_number and operator, and measurement_event_id pointing
  at its isolate's test result (meas_event_field_concept_id names that record's table).
- Times: charttime, or for the 303k rows with only a chartdate the date alone. Rows without an
  hadm_id get the visit covering their time (Laboratory Visits are created for timed ones, see
  visit_occurrence), or for date-only rows the visit covering their date (an Outpatient Visit
  for the day otherwise).
"""

import polars as pl

from mimic_omop import concepts as c
from mimic_omop.etl.events import (
    SOURCE_VALUE_LENGTH,
    coded,
    event_id,
    finish,
    with_visit_and_detail,
)
from mimic_omop.etl.intervals import assign

ISOLATE_K = 5  # k of isolate records derived from a susceptibility row (5 + target)
# the CDM 5.x field concept of each table's id, for measurement_event_id
ID_FIELD = {
    "Measurement": 1147138,  # measurement.measurement_id
    "Observation": 1147165,  # observation.observation_id
    "Procedure": 1147082,  # procedure_occurrence.procedure_occurrence_id
    "Condition": 1147127,  # condition_occurrence.condition_occurrence_id
    "Device": 1147115,  # device_exposure.device_exposure_id
    "Drug": 1147094,  # drug_exposure.drug_exposure_id
}
SPECIMEN_ID_FIELD = 1147049  # specimen.specimen_id
MIC_OPERATORS = {"<=": "<=", "=": "=", "=>": ">="}  # dilution_comparison as written in MIMIC
ISOLATE = ["micro_specimen_id", "test_seq", "org_itemid", "isolate_num"]


def normalise_codes(cvb_map: pl.LazyFrame) -> pl.LazyFrame:
    """CVB writes some of these codes with a field prefix ('microbiologyevents.test_itemid||90093',
    and one test with the spec_itemid prefix); the code is the part after the last '||'."""
    return cvb_map.with_columns(pl.col("source_code").str.split("||").list.last())


def value_concepts(cvb_map: pl.LazyFrame, name: str) -> pl.LazyFrame:
    """One value concept per code (the lowest id, as for 'Maps to value')."""
    return (
        normalise_codes(cvb_map)
        .group_by("source_code")
        .agg(pl.col("target_concept_id").min().alias(name))
    )


def _with_visits(lf: pl.LazyFrame, visits: pl.LazyFrame, details: pl.LazyFrame) -> pl.LazyFrame:
    """visit and visit_detail by charttime; date-only rows by the visit covering their date."""
    lf = with_visit_and_detail(lf, visits, details, "charttime")
    lf = assign(
        lf,
        visits.select("person_id", "visit_occurrence_id", "visit_start_date", "visit_end_date"),
        by="person_id",
        time="chartdate",
        start="visit_start_date",
        end="visit_end_date",
        interval_id="visit_occurrence_id",
        out="day_visit",
    )
    dated = pl.col("charttime").is_null()
    return lf.with_columns(visit=pl.when(dated).then("day_visit").otherwise("visit"))


def micro_events(
    micro: pl.LazyFrame,
    visits: pl.LazyFrame,
    details: pl.LazyFrame,
    test_map: pl.LazyFrame,
    org_map: pl.LazyFrame,
    ab_map: pl.LazyFrame,
    interpretation_map: pl.LazyFrame,
) -> pl.LazyFrame:
    base = _with_visits(micro.rename({"subject_id": "person_id"}), visits, details).with_columns(
        start_date=pl.coalesce(pl.col("charttime").dt.date(), "chartdate")
    )
    common = {
        "person_id": "person_id",
        "visit_occurrence_id": "visit",
        "visit_detail_id": "visit_detail",
        "start_date": "start_date",
        "start_datetime": "charttime",
        "type_concept_id": pl.lit(c.LAB),
        "concept_id": "concept_id",
        "domain_id": "domain_id",
        "source_concept_id": "source_concept_id",
    }

    # test results: rows without an antibiotic, and one derived record per isolate seen only
    # through its susceptibility rows
    is_test = pl.col("ab_itemid").is_null()
    tested = base.filter(~is_test).group_by(ISOLATE).agg(pl.all().sort_by("microevent_id").first())
    tests = pl.concat(
        [
            base.filter(is_test).with_columns(derived=pl.lit(False)),
            tested.with_columns(derived=pl.lit(True)),
        ],
        how="diagonal_relaxed",
    ).with_columns(source_code=pl.col("test_itemid").cast(pl.String))
    tests = (
        coded(
            tests,
            normalise_codes(test_map),
            on=["source_code"],
            row=["microevent_id", "derived"],
            default_domain="Measurement",
        )
        .join(
            value_concepts(org_map, "organism_concept").rename({"source_code": "org_code"}),
            left_on=pl.col("org_itemid").cast(pl.String),
            right_on="org_code",
            how="left",
        )
        .with_columns(k=pl.when("derived").then(pl.col("k") + ISOLATE_K).otherwise("k"))
    )
    has_organism = pl.col("org_itemid").is_not_null()
    test_events = finish(
        tests,
        event_id=event_id("microbiologyevents", pl.col("microevent_id")),
        **common,
        source_value="source_code",
        value_as_concept_id=pl.when(has_organism).then(pl.col("organism_concept").fill_null(0)),
        value_source_value=pl.coalesce("org_name", "comments").str.slice(0, SOURCE_VALUE_LENGTH),
    )

    # susceptibilities, linked to their isolate's derived test result (its first target)
    isolate = tests.filter("derived", pl.col("k") == ISOLATE_K).select(
        *ISOLATE,
        isolate_id=event_id("microbiologyevents", pl.col("microevent_id")),
        isolate_field=pl.col("domain_id").replace_strict(ID_FIELD, default=None),
    )
    interpretation = value_concepts(interpretation_map, "interpretation_concept")
    susceptibility = (
        coded(
            base.filter(~is_test).with_columns(source_code=pl.col("ab_itemid").cast(pl.String)),
            normalise_codes(ab_map),
            on=["source_code"],
            row=["microevent_id"],
            default_domain="Measurement",
        )
        .join(isolate, on=ISOLATE, how="left", nulls_equal=True)
        .join(
            interpretation.rename({"source_code": "interpretation"}),
            on="interpretation",
            how="left",
        )
    )
    operator = pl.col("dilution_comparison").replace_strict(MIC_OPERATORS, default=None)
    susceptibility_events = finish(
        susceptibility,
        event_id=event_id("microbiologyevents", pl.col("microevent_id")),
        **common,
        source_value="source_code",
        value_as_concept_id=pl.when(pl.col("interpretation").is_not_null()).then(
            pl.col("interpretation_concept").fill_null(0)
        ),
        value_as_number="dilution_value",
        operator_concept_id=operator.replace_strict(
            c.OPERATORS, default=None, return_dtype=pl.Int64
        ),
        value_source_value=pl.col("interpretation"),
        event_link_id="isolate_id",
        event_link_field_concept_id="isolate_field",
    )
    return pl.concat([test_events, susceptibility_events])


def specimens(
    micro: pl.LazyFrame, visits: pl.LazyFrame, details: pl.LazyFrame, spec_map: pl.LazyFrame
) -> pl.LazyFrame:
    """One specimen per micro_specimen_id, in the visit of its records; its concept from CVB's
    spec_itemid mapping (Specimen-domain targets only)."""
    concept = (
        normalise_codes(spec_map)
        .filter(pl.col("target_domain_id") == "Specimen")
        .group_by("source_code")
        .agg(
            specimen_concept_id=pl.col("target_concept_id").min(),
            specimen_source_concept_id=pl.col("source_concept_id").first(),
        )
    )
    first = micro.group_by("micro_specimen_id").agg(
        pl.col("subject_id", "hadm_id", "chartdate", "charttime", "spec_itemid", "spec_type_desc")
        .sort_by("microevent_id")
        .first()
    )
    return (
        _with_visits(first.rename({"subject_id": "person_id"}), visits, details)
        .with_columns(source_code=pl.col("spec_itemid").cast(pl.String))
        .join(concept, on="source_code", how="left")
        .select(
            specimen_id="micro_specimen_id",
            person_id="person_id",
            specimen_concept_id=pl.col("specimen_concept_id").fill_null(0),
            specimen_type_concept_id=pl.lit(c.LAB),
            specimen_date=pl.coalesce(pl.col("charttime").dt.date(), "chartdate"),
            specimen_datetime="charttime",
            specimen_source_id=pl.col("micro_specimen_id").cast(pl.String),
            specimen_source_value=pl.col("spec_type_desc").str.slice(0, SOURCE_VALUE_LENGTH),
            visit_occurrence_id="visit",
            visit_detail_id="visit_detail",
        )
    )


def specimen_links(micro: pl.LazyFrame, records: pl.LazyFrame) -> pl.LazyFrame:
    """FACT_RELATIONSHIP rows between each specimen and the test results from it, both ways.

    records: (id, domain_concept_id) of the CDM records from microbiologyevents; test results
    are those that are not susceptibilities (they have no measurement_event_id).
    """
    key = (pl.col("id") % 10**15) // 10
    pairs = records.with_columns(microevent_id=key).join(
        micro.select("microevent_id", "micro_specimen_id"), on="microevent_id"
    )
    specimen_of = pairs.select(
        domain_concept_id_1=pl.lit(c.SPECIMEN_DOMAIN),
        fact_id_1="micro_specimen_id",
        domain_concept_id_2="domain_concept_id",
        fact_id_2="id",
        relationship_concept_id=pl.lit(c.SPECIMEN_OF),
    )
    has_specimen = pairs.select(
        domain_concept_id_1="domain_concept_id",
        fact_id_1="id",
        domain_concept_id_2=pl.lit(c.SPECIMEN_DOMAIN),
        fact_id_2="micro_specimen_id",
        relationship_concept_id=pl.lit(c.HAS_SPECIMEN),
    )
    return pl.concat([specimen_of, has_specimen])
