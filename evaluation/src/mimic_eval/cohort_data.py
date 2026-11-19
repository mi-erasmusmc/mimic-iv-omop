"""Inputs of the E2b cohort evaluator (`cohorts.Data`) from the CDM and from MIMIC-IV.

CDM: each criteria domain reads its table, as Circe does; observation periods from the CDM.

Source: every source row is given the standard concepts its code maps to and is routed by the
concept's domain, the CDM's rule, so that a criteria sees the same kind of record on both sides
(a diagnosis code that maps to a procedure concept is a procedure). Mappings:
- ICD-9-CM / ICD-10-CM diagnoses, ICD-9-Proc / ICD-10-PCS procedures, CPT4 / HCPCS and NDC codes:
  Athena 'Maps to', looked up here, independently of the ETL;
- MIMIC's local codes (lab and ICU item ids, drug names): CVB as the ETL resolves it
  (`mimic_omop.vocab.Vocab`), as are lab units;
- ward administrations (eMAR): the NDC of the order they belong to (`pharmacy_id`), else the
  medication name.
Dates follow the source: a diagnosis at the admission's arrival (the earlier of admittime and
edregtime), procedures at chartdate, drugs and measurements at their own times. Admissions are
Inpatient Visits (9201) and ED transfers outside an admission Emergency Room Visits (9203); MIMIC
has no observation periods. Left out: microbiology, omr, MIMIC-IV-ED triage and vital signs (a
local mapping) and Pyxis (a derived crosswalk).
"""

from pathlib import Path

import polars as pl

from mimic_eval.cohorts import EVENT_COLUMNS, Data

INPATIENT, ER = 9201, 9203


def _scan(root: Path, table: str) -> pl.LazyFrame:
    return pl.scan_parquet(Path(root) / table / "*.parquet")


def _events(lf: pl.LazyFrame, **columns) -> pl.LazyFrame:
    """Select EVENT_COLUMNS (event_id excepted), with nulls for the ones not given."""
    exprs = []
    for name, dtype in EVENT_COLUMNS.items():
        if name == "event_id" and name not in columns:
            continue
        e = columns.get(name, pl.lit(None))
        exprs.append((pl.col(e) if isinstance(e, str) else e).cast(dtype).alias(name))
    return lf.select(exprs)


# ------------------------------------------------------------------------------------------- CDM


def cdm_data(cdm: Path) -> Data:
    s = lambda t: _scan(cdm, t)  # noqa: E731
    events = {
        "condition": _events(
            s("condition_occurrence"),
            person_id="person_id",
            event_id="condition_occurrence_id",
            concept_id="condition_concept_id",
            start_date="condition_start_date",
            end_date="condition_end_date",
            visit_id="visit_occurrence_id",
        ),
        "procedure": _events(
            s("procedure_occurrence"),
            person_id="person_id",
            event_id="procedure_occurrence_id",
            concept_id="procedure_concept_id",
            start_date="procedure_date",
            visit_id="visit_occurrence_id",
        ),
        "drug": _events(
            s("drug_exposure"),
            person_id="person_id",
            event_id="drug_exposure_id",
            concept_id="drug_concept_id",
            start_date="drug_exposure_start_date",
            end_date="drug_exposure_end_date",
            visit_id="visit_occurrence_id",
        ),
        "measurement": _events(
            s("measurement"),
            person_id="person_id",
            event_id="measurement_id",
            concept_id="measurement_concept_id",
            start_date="measurement_date",
            visit_id="visit_occurrence_id",
            value_as_number="value_as_number",
            unit_concept_id="unit_concept_id",
        ),
        "visit": _events(
            s("visit_occurrence"),
            person_id="person_id",
            event_id="visit_occurrence_id",
            concept_id="visit_concept_id",
            start_date="visit_start_date",
            end_date="visit_end_date",
            visit_id="visit_occurrence_id",
        ),
    }
    periods = s("observation_period").select(
        "person_id", op_start="observation_period_start_date", op_end="observation_period_end_date"
    )
    return Data(events, periods)


# ---------------------------------------------------------------------------------------- source


def athena_map(vocab: Path, vocabularies: list[str]) -> pl.LazyFrame:
    """(vocabulary_id, code, concept_id): codes of these vocabularies, dots removed as MIMIC
    writes them, to the valid standard concepts they map to."""
    concept = pl.scan_parquet(Path(vocab) / "athena/concept.parquet")
    relationship = pl.scan_parquet(Path(vocab) / "athena/concept_relationship.parquet").filter(
        pl.col("relationship_id") == "Maps to", pl.col("invalid_reason").is_null()
    )
    standard = concept.filter(pl.col("standard_concept") == "S", pl.col("invalid_reason").is_null())
    return (
        concept.filter(pl.col("vocabulary_id").is_in(vocabularies))
        .select(
            "vocabulary_id",
            code=pl.col("concept_code").str.replace_all(".", "", literal=True),
            concept_id_1="concept_id",
        )
        .join(relationship.select("concept_id_1", "concept_id_2"), on="concept_id_1")
        .join(standard.select(concept_id_2="concept_id"), on="concept_id_2")
        .select("vocabulary_id", "code", concept_id="concept_id_2")
        .unique()
    )


def _mapped(
    rows: pl.LazyFrame, code: pl.Expr, mapping: pl.LazyFrame, keys=("code",)
) -> pl.LazyFrame:
    """Rows joined to their codes' concepts (one row per concept), codes as strings."""
    return rows.with_columns(code=code.cast(pl.String).str.strip_chars()).join(
        mapping, on=list(keys)
    )


def source_data(source: Path, vocab: Path, needed: set[int]) -> Data:
    """MIMIC-IV events with standard concepts, restricted to the `needed` concepts (those of the
    cohorts' concept sets, which keeps the pool small)."""
    from mimic_omop.etl.drug_events import administered
    from mimic_omop.vocab import Vocab

    src = lambda t: pl.scan_parquet(Path(source) / f"{t}.parquet")  # noqa: E731
    has = lambda t: (Path(source) / f"{t}.parquet").exists()  # noqa: E731
    keep = list(needed)
    v = Vocab(vocab)

    def athena(vocabularies: list[str]) -> pl.LazyFrame:
        return athena_map(vocab, vocabularies).filter(pl.col("concept_id").is_in(keep))

    def cvb(*sets: str) -> pl.LazyFrame:
        m = pl.concat(
            [
                v.cvb_code_map(s).select(code="source_code", concept_id="target_concept_id")
                for s in sets
            ]
        )
        return m.filter(pl.col("concept_id").is_in(keep)).unique()

    def items(mapping: pl.LazyFrame) -> list[int]:
        """Item ids of a CVB mapping, to filter large tables before joining."""
        return (
            mapping.select(pl.col("code").cast(pl.Int64, strict=False))
            .drop_nulls()
            .collect()
            .to_series()
            .to_list()
        )

    admissions = src("hosp/admissions").select(
        "hadm_id", arrival=pl.min_horizontal("admittime", "edregtime").dt.date()
    )
    icd_cm = (
        athena(["ICD9CM", "ICD10CM"])
        .with_columns(
            icd_version=pl.col("vocabulary_id").replace_strict(
                {"ICD9CM": 9, "ICD10CM": 10}, return_dtype=pl.Int64
            )
        )
        .drop("vocabulary_id")
    )
    icd_proc = (
        athena(["ICD9Proc", "ICD10PCS"])
        .with_columns(
            icd_version=pl.col("vocabulary_id").replace_strict(
                {"ICD9Proc": 9, "ICD10PCS": 10}, return_dtype=pl.Int64
            )
        )
        .drop("vocabulary_id")
    )
    parts = [
        _events(
            _mapped(
                src("hosp/diagnoses_icd"), pl.col("icd_code"), icd_cm, ("code", "icd_version")
            ).join(admissions, on="hadm_id"),
            person_id="subject_id",
            concept_id="concept_id",
            start_date="arrival",
            visit_id="hadm_id",
        ),
        _events(
            _mapped(
                src("hosp/procedures_icd"), pl.col("icd_code"), icd_proc, ("code", "icd_version")
            ),
            person_id="subject_id",
            concept_id="concept_id",
            start_date="chartdate",
            visit_id="hadm_id",
        ),
        _events(
            _mapped(
                src("hosp/hcpcsevents"),
                pl.col("hcpcs_cd"),
                athena(["CPT4", "HCPCS"]).drop("vocabulary_id"),
            ),
            person_id="subject_id",
            concept_id="concept_id",
            start_date="chartdate",
            visit_id="hadm_id",
        ),
    ]

    # drugs: orders by NDC (Athena), else by drug name (CVB); ward administrations by name
    ndc = athena(["NDC"]).drop("vocabulary_id")
    names = cvb("mimiciv_hosp.prescriptions.drug")
    rx = src("hosp/prescriptions").with_row_index("_row")
    by_ndc = _mapped(rx, pl.col("ndc"), ndc)
    by_name = _mapped(rx.join(by_ndc.select("_row"), on="_row", how="anti"), pl.col("drug"), names)
    for rows in (by_ndc, by_name):
        parts.append(
            _events(
                rows,
                person_id="subject_id",
                concept_id="concept_id",
                start_date=pl.col("starttime").dt.date(),
                end_date=pl.col("stoptime").dt.date(),
                visit_id="hadm_id",
            )
        )
    # administrations: the NDC of the order they belong to (pharmacy_id), else the medication name
    emar = src("hosp/emar").filter(administered(pl.col("event_txt"))).with_row_index("_row")
    orders = (
        src("hosp/prescriptions")
        .filter(pl.col("drug_type") == "MAIN", pl.col("pharmacy_id").is_not_null())
        .group_by("pharmacy_id")
        .agg(order_ndc=pl.col("ndc").sort_by("starttime").first())
    )
    emar_by_ndc = _mapped(emar.join(orders, on="pharmacy_id"), pl.col("order_ndc"), ndc)
    emar_by_name = _mapped(
        emar.join(emar_by_ndc.select("_row"), on="_row", how="anti"), pl.col("medication"), names
    )
    parts.append(
        _events(
            pl.concat([emar_by_ndc.drop("order_ndc"), emar_by_name], how="diagonal_relaxed"),
            person_id="subject_id",
            concept_id="concept_id",
            start_date=pl.col("charttime").dt.date(),
            end_date=pl.col("charttime").dt.date(),
            visit_id="hadm_id",
        )
    )
    inputs = cvb("mimiciv_icu.inputevents.itemid")
    parts.append(
        _events(
            _mapped(
                src("icu/inputevents").filter(pl.col("itemid").is_in(items(inputs))),
                pl.col("itemid"),
                inputs,
            ),
            person_id="subject_id",
            concept_id="concept_id",
            start_date=pl.col("starttime").dt.date(),
            end_date=pl.col("endtime").dt.date(),
            visit_id="hadm_id",
        )
    )

    # measurements: labs and ICU chart values, with their units
    units = v.unit_map().select(
        unit_source_value="unit_source_value", unit_concept_id="unit_concept_id"
    )
    labs = cvb("mimiciv_hosp.d_labitems.itemid")
    chart = cvb("mimiciv_icu.d_items.label", "mimiciv_icu.chartevents.itemid")
    for table, mapping in (("hosp/labevents", labs), ("icu/chartevents", chart)):
        rows = _mapped(
            src(table).filter(pl.col("itemid").is_in(items(mapping))), pl.col("itemid"), mapping
        )
        rows = rows.with_columns(unit_source_value=pl.col("valueuom").str.strip_chars()).join(
            units, on="unit_source_value", how="left"
        )
        parts.append(
            _events(
                rows,
                person_id="subject_id",
                concept_id="concept_id",
                start_date=pl.col("charttime").dt.date(),
                visit_id="hadm_id",
                value_as_number="valuenum",
                unit_concept_id="unit_concept_id",
            )
        )

    # ICU procedures and dates
    for table, start, end in (("icu/procedureevents", "starttime", "endtime"),):
        mapping = cvb(f"mimiciv_icu.{table.split('/')[1]}.itemid", "mimiciv_icu.d_items.label")
        parts.append(
            _events(
                _mapped(
                    src(table).filter(pl.col("itemid").is_in(items(mapping))),
                    pl.col("itemid"),
                    mapping,
                ),
                person_id="subject_id",
                concept_id="concept_id",
                start_date=pl.col(start).dt.date(),
                end_date=pl.col(end).dt.date(),
                visit_id="hadm_id",
            )
        )
    dates = cvb("mimiciv_icu.datetimeevents.itemid", "mimiciv_icu.d_items.label")
    parts.append(
        _events(
            _mapped(
                src("icu/datetimeevents").filter(pl.col("itemid").is_in(items(dates))),
                pl.col("itemid"),
                dates,
            ),
            person_id="subject_id",
            concept_id="concept_id",
            start_date=pl.col("value").str.to_datetime("%Y-%m-%d %H:%M:%S").dt.date(),
            visit_id="hadm_id",
        )
    )

    # MIMIC-IV-ED diagnoses and medicine reconciliation, if converted
    if has("ed/edstays"):
        stays = src("ed/edstays").select("stay_id", "subject_id", "hadm_id", "intime")
        ed_dx = (
            src("ed/diagnosis")
            .select("stay_id", "icd_code", "icd_version")
            .join(stays, on="stay_id")
        )
        parts.append(
            _events(
                _mapped(ed_dx, pl.col("icd_code"), icd_cm, ("code", "icd_version")),
                person_id="subject_id",
                concept_id="concept_id",
                start_date=pl.col("intime").dt.date(),
                visit_id=pl.coalesce("hadm_id", "stay_id"),
            )
        )
        medrecon = (
            src("ed/medrecon")
            .filter(pl.col("etc_rn") == 1)
            .join(stays.select("stay_id", "hadm_id"), on="stay_id")
        )
        parts.append(
            _events(
                _mapped(medrecon, pl.col("ndc"), ndc),
                person_id="subject_id",
                concept_id="concept_id",
                start_date=pl.col("charttime").dt.date(),
                visit_id=pl.coalesce("hadm_id", "stay_id"),
            )
        )

    pool = (
        pl.concat(parts, how="vertical_relaxed")
        .collect(engine="streaming")
        .with_row_index("event_id")
        .with_columns(pl.col("event_id").cast(pl.Int64))
        .select(list(EVENT_COLUMNS))
        .lazy()
    )
    visits = pl.concat(
        [
            _events(
                src("hosp/admissions"),
                person_id="subject_id",
                event_id="hadm_id",
                concept_id=pl.lit(INPATIENT),
                start_date=pl.min_horizontal("admittime", "edregtime").dt.date(),
                end_date=pl.col("dischtime").dt.date(),
                visit_id="hadm_id",
            ),
            _events(
                src("hosp/transfers").filter(
                    pl.col("eventtype") == "ED", pl.col("hadm_id").is_null()
                ),
                person_id="subject_id",
                event_id="transfer_id",
                concept_id=pl.lit(ER),
                start_date=pl.col("intime").dt.date(),
                end_date=pl.coalesce("outtime", "intime").dt.date(),
                visit_id="transfer_id",
            ),
        ]
    )
    # route by the concept's domain, as the CDM does, so that a criteria sees the same kind of
    # record on both sides (a diagnosis code mapped to a procedure concept is a procedure)
    domains = (
        pl.scan_parquet(Path(vocab) / "athena/concept.parquet")
        .filter(pl.col("concept_id").is_in(keep))
        .select("concept_id", domain=pl.col("domain_id"))
    )
    routed = pool.join(domains, on="concept_id")
    events = {
        d: routed.filter(pl.col("domain") == d.capitalize()).drop("domain")
        for d in ("condition", "procedure", "drug", "measurement")
    }
    events["visit"] = visits
    return Data(events, None)
