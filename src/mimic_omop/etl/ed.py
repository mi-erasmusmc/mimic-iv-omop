"""MIMIC-IV-ED: diagnoses, triage, vital signs, medicine reconciliation and Pyxis dispensing of
emergency department stays, as coded events.

Every ED stay (ed.edstays) starts at the intime of an ED transfer in hosp.transfers, which the
ETL has already made a visit_detail of, inside an ED-only visit or the admission it led to; ED
records belong to that visit (`stay_links`). The 76 MIMIC-IV-ED 2.2 stays of patients who are not
in MIMIC-IV 3.1 have no such transfer, and their records are left out.

Conventions:
- Diagnoses: ICD-9-CM / ICD-10-CM through Athena, CVB for codes Athena does not map (as
  hosp.diagnoses_icd), dated at the ED stay's intime; seq_num 1 is the primary diagnosis.
- Triage and vital signs: the ETL's local mapping mappings/ed.csv (LOINC and UCUM codes,
  resolved through Athena), one record per value. Triage values are dated at the ED intime.
  Text values that are not numbers (pain 'unable', 'sleeping') keep their text and no number;
  the chief complaint and the heart rhythm are text.
- Medicine reconciliation (the patient's medications before the ED stay): drug_exposure typed
  EHR medication list (32830), dated when documented; concept from the NDC through Athena, else
  from the GSN crosswalk. medrecon repeats a medication once per drug class (etc_rn); the
  first row stands for it.
- Pyxis dispensing: drug_exposure typed EHR dispensing record (32825); concept from the GSN
  crosswalk (Pyxis has no NDC). A medication is listed once per GSN (gsn_rn); the first GSN
  that maps gives the concept, and the row with gsn_rn 1 stands for it.
- The GSN crosswalk (`gsn_map`): Athena has no GSN (First Databank Generic Sequence Number)
  vocabulary, but hosp.prescriptions records GSN and NDC together. Each GSN maps to the RxNorm
  Clinical Drug its prescriptions' NDCs map to (a branded or quantified drug is replaced by its
  Clinical Drug ancestor), the most frequent if several; see review R32.
"""

import io
from importlib.resources import files

import polars as pl

from mimic_omop import concepts as c
from mimic_omop.etl.events import (
    EVENT_DOMAINS,
    SOURCE_VALUE_LENGTH,
    VALUE_STRING_LENGTH,
    coded,
    event_id,
    finish,
    icd_key,
    with_visit_and_detail,
)

VITALS = ["temperature", "heartrate", "resprate", "o2sat", "sbp", "dbp", "pain"]
TRIAGE = [*VITALS, "acuity", "chiefcomplaint"]
VITALSIGN = [*VITALS, "rhythm"]
TEXT_ITEMS = {"chiefcomplaint", "rhythm"}  # kept as text, never as a number
ROWS_PER_STAY = 1000  # rows of one ED stay in a table (at most 193 in MIMIC-IV-ED 2.2)


def local_mapping() -> pl.LazyFrame:
    """mappings/ed.csv: result_name (the ED column), component, loinc, ucum."""
    text = files("mimic_omop.mappings").joinpath("ed.csv").read_text()
    return pl.read_csv(io.StringIO(text), infer_schema=False).lazy()


def stay_links(edstays: pl.LazyFrame, details: pl.LazyFrame) -> pl.LazyFrame:
    """stay_id -> person_id, intime, the visit of the ED stay and its first care unit stay: the
    visit_detail starting at the ED intime (of two starting then, the lower id)."""
    return (
        edstays.select("stay_id", "intime", person_id="subject_id")
        .join(
            details.select(
                "person_id",
                "visit_detail_id",
                "visit_occurrence_id",
                intime="visit_detail_start_datetime",
            ),
            on=["person_id", "intime"],
        )
        .sort("visit_detail_id")
        .unique("stay_id", keep="first")
        .select("stay_id", "person_id", "intime", ed_visit="visit_occurrence_id",
                ed_detail="visit_detail_id")
    )  # fmt: skip


def _usable(target: str, domain: str) -> dict[str, pl.Expr]:
    """concept_id and domain_id as `coded` sets them, for maps joined by hand."""
    ok = pl.col(domain).is_in(EVENT_DOMAINS).fill_null(False)
    return {
        "concept_id": pl.when(ok).then(pl.col(target)).otherwise(0),
        "domain_id": pl.when(ok).then(pl.col(domain)).otherwise(pl.lit("Drug")),
    }


def diagnoses(diagnosis: pl.LazyFrame, links: pl.LazyFrame, icd_map: pl.LazyFrame) -> pl.LazyFrame:
    lf = diagnosis.join(links, on="stay_id").with_columns(
        vocabulary_id=pl.when(pl.col("icd_version") == 9)
        .then(pl.lit("ICD9CM"))
        .otherwise(pl.lit("ICD10CM")),
        source_code=pl.col("icd_code").str.strip_chars(),
    )
    lf = coded(
        lf,
        icd_map,
        on=["vocabulary_id", "source_code"],
        row=["stay_id", "seq_num", "icd_version"],
        default_domain="Condition",
    )
    return finish(
        lf,
        event_id=event_id(
            "ed_diagnosis", icd_key(pl.col("stay_id"), pl.col("seq_num"), pl.col("icd_version"))
        ),
        person_id="person_id",
        visit_occurrence_id="ed_visit",
        visit_detail_id="ed_detail",
        start_date=pl.col("intime").dt.date(),
        start_datetime="intime",
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


def _values(lf: pl.LazyFrame, items: list[str], mapping: pl.LazyFrame, index: list[str]):
    """One row per non-empty value of the wide `items` columns: result_name, item (its position
    in `items`), text, number; with the mapping's target and unit."""
    position = {name: i for i, name in enumerate(items)}
    text = pl.col("text").str.strip_chars()
    return (
        lf.with_columns(pl.col(items).cast(pl.String))
        .unpivot(index=index, on=items, variable_name="result_name", value_name="text")
        .filter(text.is_not_null() & (text != ""))
        .with_columns(
            item=pl.col("result_name").replace_strict(position, return_dtype=pl.Int64),
            text=text,
            number=pl.when(~pl.col("result_name").is_in(TEXT_ITEMS)).then(
                text.cast(pl.Float64, strict=False)
            ),
            k=pl.lit(0, pl.Int64),
        )
        .join(mapping.select("result_name", "target_concept_id", "target_domain_id",
                             "unit_concept_id"), on="result_name", how="left")
    )  # fmt: skip


def _value_events(lf: pl.LazyFrame, source: str, key: pl.Expr, time: str) -> pl.LazyFrame:
    is_text = pl.col("result_name").is_in(TEXT_ITEMS)
    return finish(
        lf,
        event_id=event_id(source, key),
        person_id="person_id",
        visit_occurrence_id="ed_visit",
        visit_detail_id="visit_detail",
        start_date=pl.col(time).dt.date(),
        start_datetime=time,
        type_concept_id=pl.lit(c.EHR),
        concept_id=pl.col("target_concept_id").fill_null(0),
        domain_id=pl.col("target_domain_id").fill_null("Measurement"),
        source_value="result_name",
        source_concept_id=pl.lit(0),
        value_as_number="number",
        unit_concept_id="unit_concept_id",
        value_source_value=pl.col("text").str.slice(0, SOURCE_VALUE_LENGTH),
        value_as_string=pl.when(is_text).then(pl.col("text").str.slice(0, VALUE_STRING_LENGTH)),
    )


def triage(triage: pl.LazyFrame, links: pl.LazyFrame, mapping: pl.LazyFrame) -> pl.LazyFrame:
    """Triage values, at the ED stay's intime and in its first care unit stay."""
    lf = _values(triage.join(links, on="stay_id"), TRIAGE, mapping,
                 ["stay_id", "person_id", "intime", "ed_visit", "ed_detail"])  # fmt: skip
    return _value_events(
        lf.with_columns(visit_detail="ed_detail"),
        "ed_triage",
        pl.col("stay_id") * 10 + pl.col("item"),
        "intime",
    )


def vital_signs(
    vitalsign: pl.LazyFrame,
    links: pl.LazyFrame,
    visits: pl.LazyFrame,
    details: pl.LazyFrame,
    mapping: pl.LazyFrame,
) -> pl.LazyFrame:
    """Vital signs, each in the care unit stay of the ED visit covering its charttime."""
    rows = vitalsign.join(links, on="stay_id").with_columns(
        seq=pl.int_range(pl.len()).over(
            "stay_id", order_by=["charttime", "heartrate", "sbp", "resprate", "temperature"]
        )
    )
    lf = _values(rows, VITALSIGN, mapping,
                 ["stay_id", "seq", "person_id", "charttime", "ed_visit"])  # fmt: skip
    lf = with_visit_and_detail(lf.with_columns(hadm_id="ed_visit"), visits, details, "charttime")
    key = (pl.col("stay_id") * ROWS_PER_STAY + pl.col("seq")) * 10 + pl.col("item")
    return _value_events(lf, "ed_vitalsign", key, "charttime")


def gsn_map(
    prescriptions: pl.LazyFrame,
    ndc_map: pl.LazyFrame,
    concept: pl.LazyFrame,
    concept_ancestor: pl.LazyFrame,
) -> pl.LazyFrame:
    """GSN -> target_concept_id, target_domain_id, from the GSN and NDC that hosp.prescriptions
    records together (all prescriptions, so the crosswalk does not depend on person buckets)."""
    pairs = (
        prescriptions.select("gsn", "ndc")
        .drop_nulls()
        .with_columns(pl.col("gsn").str.split(" "))
        .explode("gsn")
        .filter(~pl.col("gsn").is_in(["", "0"]))  # 0 means missing
        .join(
            ndc_map.filter(pl.col("target_concept_id").is_not_null()).select(
                ndc="source_code", target="target_concept_id"
            ),
            on="ndc",
        )
    )
    clinical = concept.filter(pl.col("concept_class_id") == "Clinical Drug").select(
        clinical="concept_id"
    )
    to_clinical = (
        concept_ancestor.join(clinical, left_on="ancestor_concept_id", right_on="clinical")
        .select(target="descendant_concept_id", clinical="ancestor_concept_id")
        .join(pairs.select("target").unique(), on="target", how="semi")
        .group_by("target")
        .agg(pl.col("clinical").min())
    )
    domains = concept.select(target_concept_id="concept_id", target_domain_id="domain_id")
    return (
        pairs.join(to_clinical, on="target", how="left")
        .with_columns(target_concept_id=pl.coalesce("clinical", "target"))
        .group_by("gsn", "target_concept_id")
        .agg(n=pl.len())
        .sort(["n", "target_concept_id"], descending=[True, False])
        .group_by("gsn", maintain_order=True)
        .first()
        .join(domains, on="target_concept_id")
        .select("gsn", "target_concept_id", "target_domain_id")
    )


def _drug_events(lf: pl.LazyFrame, source: str, type_concept: int) -> pl.LazyFrame:
    return finish(
        lf,
        event_id=event_id(source, pl.col("stay_id") * ROWS_PER_STAY + pl.col("seq")),
        person_id="person_id",
        visit_occurrence_id="ed_visit",
        visit_detail_id="visit_detail",
        start_date=pl.col("charttime").dt.date(),
        start_datetime="charttime",
        end_date=pl.col("charttime").dt.date(),
        end_datetime="charttime",
        type_concept_id=pl.lit(type_concept),
        concept_id="concept_id",
        domain_id="domain_id",
        source_value=pl.col("name").str.slice(0, SOURCE_VALUE_LENGTH),
        source_concept_id="source_concept_id",
    )


def medrecon(
    medrecon: pl.LazyFrame,
    links: pl.LazyFrame,
    visits: pl.LazyFrame,
    details: pl.LazyFrame,
    ndc_map: pl.LazyFrame,
    gsns: pl.LazyFrame,
) -> pl.LazyFrame:
    ndc = ndc_map.filter(pl.col("target_concept_id").is_not_null()).select(
        ndc="source_code",
        ndc_source="source_concept_id",
        ndc_target="target_concept_id",
        ndc_domain="target_domain_id",
    )
    lf = (
        medrecon.filter(pl.col("etc_rn") == 1)
        .join(links, on="stay_id")
        .with_columns(
            seq=pl.int_range(pl.len()).over("stay_id", order_by=["charttime", "name", "ndc", "gsn"])
        )
        .join(ndc, on="ndc", how="left")
        .join(
            gsns.rename({"target_concept_id": "gsn_target", "target_domain_id": "gsn_domain"}),
            on="gsn",
            how="left",
        )  # fmt: skip
        .with_columns(
            target=pl.coalesce("ndc_target", "gsn_target"),
            domain=pl.when(pl.col("ndc_target").is_not_null())
            .then("ndc_domain")
            .otherwise("gsn_domain"),
            source_concept_id=pl.col("ndc_source").fill_null(0),
        )
        .with_columns(k=pl.int_range(pl.len()).over("stay_id", "seq", order_by="target"))
        .with_columns(**_usable("target", "domain"))
    )
    lf = with_visit_and_detail(lf.with_columns(hadm_id="ed_visit"), visits, details, "charttime")
    return _drug_events(lf, "ed_medrecon", c.EHR_MEDICATION_LIST)


def pyxis(
    pyxis: pl.LazyFrame,
    links: pl.LazyFrame,
    visits: pl.LazyFrame,
    details: pl.LazyFrame,
    gsns: pl.LazyFrame,
) -> pl.LazyFrame:
    med = ["stay_id", "charttime", "med_rn"]
    rows = pyxis.join(links, on="stay_id").join(gsns, on="gsn", how="left")
    # the first GSN, by gsn_rn, that the crosswalk maps
    mapped = (
        rows.filter(pl.col("target_concept_id").is_not_null())
        .sort("gsn_rn")
        .group_by(med)
        .agg(target=pl.col("target_concept_id").first(), domain=pl.col("target_domain_id").first())
    )
    lf = (
        rows.filter(pl.col("gsn_rn") == 1)
        .join(mapped, on=med, how="left")
        .with_columns(
            seq=pl.int_range(pl.len()).over("stay_id", order_by=["charttime", "med_rn"]),
            k=pl.lit(0, pl.Int64),
            source_concept_id=pl.lit(0, pl.Int64),
        )
        .with_columns(**_usable("target", "domain"))
    )
    lf = with_visit_and_detail(lf.with_columns(hadm_id="ed_visit"), visits, details, "charttime")
    return _drug_events(lf, "ed_pyxis", c.EHR_DISPENSING)
