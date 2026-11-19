"""Drug events: orders (hosp.prescriptions), ward administrations (hosp.emar) and ICU
administrations (icu.inputevents). All three go to drug_exposure, told apart by type concept.

Conventions (THEMIS drug_exposure pages):
- Orders are typed EHR order and start at the order's start; their end is the order's stop time,
  not before the start (verbatim_end_date keeps the stop date as recorded). An order without a
  start time starts when the pharmacy started or entered it (hosp.pharmacy).
- Discharge medications (pharmacy proc_type 'Discharge Med') are prescriptions for after
  discharge: typed EHR prescription, starting when entered, and ending at their stop time or else
  29 days later (THEMIS default for a written prescription without days supply).
- Administrations are typed EHR administration record. Only actions meaning the drug was given
  become records; missed, held or refused doses do not (ADMINISTERED_ACTIONS). eMAR records
  have no duration (end = start); ICU input events end at their endtime, and a rate change is
  already a new input event in MIMIC.
- days_supply and sig are left empty: MIMIC has no verbatim days supply (THEMIS: do not
  calculate it) and no verbatim sig. quantity is filled for eMAR administrations of a single
  product: the amount of the product given (hosp.emar_detail: 1 TAB, 2 TAB, 30 mL), with its unit
  in dose_unit_source_value; with the drug concept's strength (DRUG_STRENGTH) it gives the dose.
  An administration of several products (two tablet strengths making up one dose) has none, as
  their amounts do not add up to a quantity of one drug concept.

Drug concepts:
- Orders: the first of NDC via Athena, NDC via CVB, the drug name via CVB, the formulary code via
  CVB that maps the order.
- eMAR: the concept of the linked order (pharmacy_id, its MAIN component), else the medication
  name via CVB's drug names, then CVB's product names (gcpt_drug_ndc).
- Input events: CVB's itemid mapping.
"""

import polars as pl

from mimic_omop import concepts as c
from mimic_omop.etl.events import (
    SOURCE_VALUE_LENGTH,
    coded,
    event_id,
    finish,
    icu_stay_details,
    with_icu_stay_detail,
    with_visit_and_detail,
)

# eMAR event_txt values (after removing a 'Delayed ' prefix and an ' in Other Location' suffix)
# that mean the drug was given. Anything else, including actions MIMIC adds later, is excluded.
# 'Confirmed' (a nurse confirming that an infusion is still running) re-documents an
# administration already recorded, so it is not one (review R18).
ADMINISTERED_ACTIONS = (
    "Administered",
    "Administered Bolus from IV Drip",
    "Partial Administered",
    "Started",
    "Restarted",
    "Rate Change",
    "Applied",
    "Removed Existing / Applied New",
    "Flushed",
)
# input events whose order was rewritten were not given as recorded (a MIMIC-III status; MIMIC-IV
# 3.1 has none)
NOT_GIVEN_INPUT_STATUS = ("Rewritten",)


def first_mapping(
    lf: pl.LazyFrame, candidates: list[tuple[str, pl.LazyFrame]]
) -> tuple[pl.LazyFrame, pl.LazyFrame]:
    """Pick, per row, the first candidate (key column, code map) whose map has the row's key.

    Returns the rows with `via` (the candidate's index) and `source_code` (its key value), and the
    candidates' maps stacked with their `via`, to be joined on both by `coded`.
    """
    maps = []
    for i, (column, mapping) in enumerate(candidates):
        m = mapping.filter(pl.col("target_concept_id").is_not_null()).select(
            pl.lit(i, pl.Int8).alias("via"),
            "source_code",
            "source_concept_id",
            "target_concept_id",
            "target_domain_id",
        )
        maps.append(m)
        lf = lf.join(
            m.select(pl.col("source_code").alias(column)).unique().with_columns(pl.lit(True).alias(f"_has{i}")),
            on=column,
            how="left",
        )  # fmt: skip
    via = pl.lit(None, pl.Int8)
    code = pl.lit(None, pl.String)
    for i in reversed(range(len(candidates))):
        via = pl.when(pl.col(f"_has{i}")).then(pl.lit(i, pl.Int8)).otherwise(via)
        code = pl.when(pl.col(f"_has{i}")).then(pl.col(candidates[i][0])).otherwise(code)
    lf = lf.with_columns(via=via, source_code=code).drop(f"_has{i}" for i in range(len(candidates)))
    return lf, pl.concat(maps)


DISCHARGE_MED = "Discharge Med"  # hosp.pharmacy.proc_type
PRESCRIPTION_DAYS = 29  # THEMIS: a written prescription without days supply ends 29 days later


def prescription_codes(
    prescriptions: pl.LazyFrame,
    ndc_athena: pl.LazyFrame,
    ndc_cvb: pl.LazyFrame,
    drug_cvb: pl.LazyFrame,
    formulary_cvb: pl.LazyFrame,
    pharmacy: pl.LazyFrame | None = None,
) -> pl.LazyFrame:
    """Orders with their drug concepts: one row per order and target, with `start` (the order's
    start, else the pharmacy's start or entry time), `discharge_med`, `seq` (the order's position
    in its admission), `k`, concept_id, domain_id and source_concept_id."""
    if pharmacy is not None:
        prescriptions = prescriptions.join(
            pharmacy.select(
                "pharmacy_id", "proc_type", "entertime", pharmacy_starttime="starttime"
            ),
            on="pharmacy_id",
            how="left",
        )
    else:
        prescriptions = prescriptions.with_columns(
            proc_type=pl.lit(None, pl.String),
            entertime=pl.lit(None, pl.Datetime("us")),
            pharmacy_starttime=pl.lit(None, pl.Datetime("us")),
        )
    prescriptions = prescriptions.with_columns(
        start=pl.coalesce("starttime", "pharmacy_starttime", "entertime"),
        discharge_med=(pl.col("proc_type") == DISCHARGE_MED).fill_null(False),
    )
    ndc = pl.col("ndc").str.strip_chars()
    order = ["start", "pharmacy_id", "drug_type", "drug", "ndc", "formulary_drug_cd", "poe_seq"]
    lf = prescriptions.with_columns(
        ndc_code=pl.when(ndc.is_not_null() & (ndc != "0")).then(ndc),
        drug_code=pl.col("drug").str.strip_chars(),
        formulary_code=pl.col("formulary_drug_cd").str.strip_chars(),
        seq=pl.int_range(pl.len()).over("hadm_id", order_by=order),
    )
    lf, maps = first_mapping(
        lf,
        [
            ("ndc_code", ndc_athena),
            ("ndc_code", ndc_cvb),
            ("drug_code", drug_cvb),
            ("formulary_code", formulary_cvb),
        ],
    )
    return coded(lf, maps, on=["via", "source_code"], row=["hadm_id", "seq"], default_domain="Drug")


def orders(
    coded_prescriptions: pl.LazyFrame,
    visits: pl.LazyFrame,
    details: pl.LazyFrame,
    routes: pl.LazyFrame,
) -> pl.LazyFrame:
    lf = with_visit_and_detail(
        coded_prescriptions.rename({"subject_id": "person_id"}), visits, details, "start"
    ).join(
        routes.select(route="source_code", route_concept_id="target_concept_id"),
        on="route",
        how="left",
    )
    end = (
        pl.when(pl.col("discharge_med") & pl.col("stoptime").is_null())
        .then(pl.col("start") + pl.duration(days=PRESCRIPTION_DAYS))
        .otherwise(pl.max_horizontal("stoptime", "start"))
    )
    return finish(
        lf,
        event_id=event_id("prescriptions", pl.col("hadm_id") * 10**5 + pl.col("seq")),
        person_id="person_id",
        visit_occurrence_id="visit",
        visit_detail_id="visit_detail",
        start_date=pl.col("start").dt.date(),
        start_datetime="start",
        end_date=end.dt.date(),
        end_datetime=end,
        verbatim_end_date=pl.col("stoptime").dt.date(),
        type_concept_id=pl.when("discharge_med").then(c.EHR_PRESCRIPTION).otherwise(c.EHR_ORDER),
        concept_id="concept_id",
        domain_id="domain_id",
        source_value=pl.col("drug").str.slice(0, SOURCE_VALUE_LENGTH),
        source_concept_id="source_concept_id",
        route_concept_id="route_concept_id",
        route_source_value="route",
    )


def administered(event_txt: pl.Expr) -> pl.Expr:
    action = (
        event_txt.str.strip_chars()
        .str.strip_prefix("Delayed ")
        .str.strip_suffix(" in Other Location")
    )
    return action.is_in(ADMINISTERED_ACTIONS).fill_null(False)


def product_quantities(emar_detail: pl.LazyFrame) -> pl.LazyFrame:
    """emar_id -> quantity, dose_unit_source_value: the amount and unit of the product given, for
    administrations of exactly one product (one detail row below the administration's own)."""
    amount = pl.col("product_amount_given").str.strip_chars().cast(pl.Float64, strict=False)
    unit = pl.col("product_unit").str.strip_chars()
    known = amount.is_not_null() & unit.is_not_null() & (unit != "")
    return (
        emar_detail.filter(pl.col("parent_field_ordinal").is_not_null())
        .group_by("emar_id")
        .agg(
            n=pl.len(),
            quantity=amount.first(),
            dose_unit_source_value=unit.first(),
            known=known.first(),
        )
        .filter(pl.col("n") == 1, pl.col("known"))
        .select("emar_id", "quantity", "dose_unit_source_value")
    )


def emar_administrations(
    emar: pl.LazyFrame,
    emar_detail: pl.LazyFrame,
    coded_prescriptions: pl.LazyFrame,
    visits: pl.LazyFrame,
    details: pl.LazyFrame,
    routes: pl.LazyFrame,
    drug_cvb: pl.LazyFrame,
    product_cvb: pl.LazyFrame,
) -> pl.LazyFrame:
    given = (
        emar.filter(administered(pl.col("event_txt")))
        .rename({"subject_id": "person_id"})
        .join(product_quantities(emar_detail), on="emar_id", how="left")
    )
    # the order an administration belongs to: its MAIN component (an IV mix also has BASE rows)
    order = (
        coded_prescriptions.filter(pl.col("drug_type") == "MAIN")
        .with_columns(first=pl.col("seq").min().over("pharmacy_id"))
        .filter(pl.col("seq") == pl.col("first"))
        .join(
            routes.select(route="source_code", route_concept_id="target_concept_id"),
            on="route",
            how="left",
        )
        .select(
            "pharmacy_id", "k", "concept_id", "domain_id", "source_concept_id",
            "route_concept_id", route_source_value="route",
        )
    )  # fmt: skip
    linked = given.join(order, on="pharmacy_id", how="inner")
    unlinked, maps = first_mapping(
        given.join(order.select("pharmacy_id").unique(), on="pharmacy_id", how="anti").with_columns(
            medication_code=pl.col("medication").str.strip_chars()
        ),
        [("medication_code", drug_cvb), ("medication_code", product_cvb)],
    )
    unlinked = coded(
        unlinked, maps, on=["via", "source_code"], row=["emar_id"], default_domain="Drug"
    )
    lf = with_visit_and_detail(
        pl.concat([linked, unlinked], how="diagonal_relaxed"), visits, details, "charttime"
    )
    return finish(
        lf,
        event_id=event_id("emar", pl.col("person_id") * 10**6 + pl.col("emar_seq")),
        person_id="person_id",
        visit_occurrence_id="visit",
        visit_detail_id="visit_detail",
        start_date=pl.col("charttime").dt.date(),
        start_datetime="charttime",
        end_date=pl.col("charttime").dt.date(),
        end_datetime="charttime",
        type_concept_id=pl.lit(c.EHR_ADMINISTRATION),
        concept_id="concept_id",
        domain_id="domain_id",
        source_value=pl.col("medication").str.slice(0, SOURCE_VALUE_LENGTH),
        source_concept_id="source_concept_id",
        route_concept_id="route_concept_id",
        route_source_value="route_source_value",
        quantity="quantity",
        dose_unit_source_value="dose_unit_source_value",
    )


def icu_inputs(
    inputevents: pl.LazyFrame,
    visits: pl.LazyFrame,
    details: pl.LazyFrame,
    icustays: pl.LazyFrame,
    item_map: pl.LazyFrame,
) -> pl.LazyFrame:
    lf = (
        inputevents.filter(~pl.col("statusdescription").is_in(NOT_GIVEN_INPUT_STATUS).fill_null(False))
        .rename({"subject_id": "person_id"})
        .with_columns(
            source_code=pl.col("itemid").cast(pl.String),
            seq=pl.int_range(pl.len()).over(
                "stay_id", order_by=["starttime", "itemid", "orderid", "endtime"]
            ),
        )
    )  # fmt: skip
    lf = coded(lf, item_map, on=["source_code"], row=["stay_id", "seq"], default_domain="Drug")
    lf = with_visit_and_detail(lf, visits, details, "starttime")
    lf = with_icu_stay_detail(lf, icu_stay_details(icustays, details), "starttime")
    end = pl.max_horizontal("endtime", "starttime")
    return finish(
        lf,
        event_id=event_id("inputevents", pl.col("stay_id") * 10**5 + pl.col("seq")),
        person_id="person_id",
        visit_occurrence_id="visit",
        visit_detail_id="visit_detail",
        start_date=pl.col("starttime").dt.date(),
        start_datetime="starttime",
        end_date=end.dt.date(),
        end_datetime=end,
        verbatim_end_date=pl.col("endtime").dt.date(),
        type_concept_id=pl.lit(c.EHR_ADMINISTRATION),
        concept_id="concept_id",
        domain_id="domain_id",
        source_value="source_code",
        source_concept_id="source_concept_id",
    )
