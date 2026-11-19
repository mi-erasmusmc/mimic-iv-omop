"""The registry of coded event sources, and selection of their events by domain.

Each CDM event table takes the events of its domain from every source that can produce it (see
etl/events.py for how sources become events).
"""

import shutil
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import quote

import polars as pl

from mimic_omop.context import Inputs
from mimic_omop.etl import chart_events, drug_events, ed, microbiology, omr
from mimic_omop.etl.events import (
    DOMAIN_TABLE,
    EVENT_SCHEMA,
    diagnoses,
    drgs,
    hcpcs,
    labs,
    procedures,
)
from mimic_omop.source import Source


@dataclass(frozen=True)
class EventSource:
    name: str
    default_domain: str
    mapping: Callable[[Inputs], pl.LazyFrame]
    build: Callable[[Inputs, pl.LazyFrame], pl.LazyFrame]

    def domains(self, inp: Inputs) -> set[str]:
        """Domains this source can produce: its mapping's target domains and its default."""
        found = self.mapping(inp).select(pl.col("target_domain_id").unique()).collect()
        return {self.default_domain, *found.to_series().drop_nulls()}


def _diagnoses_map(inp: Inputs) -> pl.LazyFrame:
    return inp.vocab.code_map(
        ("ICD9CM", "ICD10CM"), "mimiciv_hosp.d_icd_diagnoses.icd_code", strip_dots=True
    )


def _procedures_map(inp: Inputs) -> pl.LazyFrame:
    return inp.vocab.code_map(
        ("ICD9Proc", "ICD10PCS"), "mimiciv_hosp.d_icd_procedures.icd_code", strip_dots=True
    )


def _drug_maps(inp: Inputs) -> dict[str, pl.LazyFrame]:
    v = inp.vocab
    return {
        "ndc_athena": v.code_map(("NDC",)),
        "ndc_cvb": v.cvb_code_map("mimiciv_hosp.prescriptions.ndc"),
        "drug_cvb": v.cvb_code_map("mimiciv_hosp.prescriptions.drug"),
        "formulary_cvb": v.cvb_code_map("mimiciv_hosp.prescriptions.formulary_drug_cd"),
        "product_cvb": v.cvb_code_map("gcpt_drug_ndc"),
    }


def _all_targets(maps: dict[str, pl.LazyFrame]) -> pl.LazyFrame:
    return pl.concat([m.select("target_domain_id") for m in maps.values()])


def _coded_prescriptions(inp: Inputs) -> pl.LazyFrame:
    m = _drug_maps(inp)
    return drug_events.prescription_codes(
        inp.src.scan("hosp/prescriptions"),
        m["ndc_athena"],
        m["ndc_cvb"],
        m["drug_cvb"],
        m["formulary_cvb"],
        inp.src.scan("hosp/pharmacy"),
    )


def _routes(inp: Inputs) -> pl.LazyFrame:
    return inp.vocab.lookup("mimiciv_hosp.prescriptions.route", domains=("Route",))


def _icu_table_items(table: str) -> Callable[[Inputs], pl.LazyFrame]:
    """CVB's item mapping of an ICU table, and its ICU item labels for the items it lacks there
    (both keyed by itemid; they do not overlap for procedureevents and datetimeevents)."""

    def mapping(inp: Inputs) -> pl.LazyFrame:
        v = inp.vocab
        return pl.concat(
            [
                v.cvb_code_map(f"mimiciv_icu.{table}.itemid"),
                v.cvb_code_map("mimiciv_icu.d_items.label"),
            ]
        ).unique(["source_code", "target_concept_id"])

    return mapping


def _ed(build: Callable[[Inputs], pl.LazyFrame]) -> Callable[[Inputs, pl.LazyFrame], pl.LazyFrame]:
    """An ED source's builder, giving no events when MIMIC-IV-ED was not converted."""

    def run(inp: Inputs, _mapping: pl.LazyFrame) -> pl.LazyFrame:
        if not inp.src.has("ed/edstays"):
            return pl.LazyFrame(schema=EVENT_SCHEMA)
        return build(inp)

    return run


def _ed_links(inp: Inputs) -> pl.LazyFrame:
    return ed.stay_links(inp.src.scan("ed/edstays"), inp.cdm.scan("visit_detail"))


def _ed_vitals_map(inp: Inputs) -> pl.LazyFrame:
    return omr.omr_map(ed.local_mapping(), inp.vocab.athena("concept"))


_GSN_MAPS: dict[Path, pl.DataFrame] = {}


def _gsn_map(inp: Inputs) -> pl.LazyFrame:
    """The GSN crosswalk, from all prescriptions (not the bucket's), computed once per source."""
    root = inp.src.root
    if root not in _GSN_MAPS:
        _GSN_MAPS[root] = ed.gsn_map(
            Source(root).scan("hosp/prescriptions"),
            inp.vocab.code_map(("NDC",)),
            inp.vocab.athena("concept"),
            inp.vocab.athena("concept_ancestor"),
        ).collect()
    return _GSN_MAPS[root].lazy()


def _ed_drug_map(inp: Inputs) -> pl.LazyFrame:
    return pl.concat(
        [
            inp.vocab.code_map(("NDC",)).select("target_domain_id"),
            _gsn_map(inp).select("target_domain_id"),
        ]
    )


MICRO = "mimiciv_hosp.microbiologyevents"


def _micro_codes(inp: Inputs) -> pl.LazyFrame:
    """The test and antibiotic code maps: the concepts microbiology events are recorded as."""
    v = inp.vocab
    return pl.concat(
        [v.cvb_code_map(f"{MICRO}.test_itemid"), v.cvb_code_map(f"{MICRO}.ab_itemid")],
        how="diagonal_relaxed",
    )


def _icu_items(inp: Inputs) -> pl.LazyFrame:
    """CVB's two ICU item sets, both keyed by itemid (they do not conflict)."""
    v = inp.vocab
    return pl.concat(
        [
            v.cvb_code_map("mimiciv_icu.d_items.label"),
            v.cvb_code_map("mimiciv_icu.chartevents.itemid"),
        ]
    ).unique(["source_code", "target_concept_id"])


SOURCES = (
    EventSource(
        "diagnoses_icd",
        "Condition",
        _diagnoses_map,
        lambda inp, m: diagnoses(
            inp.src.scan("hosp/diagnoses_icd"), inp.cdm.scan("visit_occurrence"), m
        ),
    ),
    EventSource(
        "procedures_icd",
        "Procedure",
        _procedures_map,
        lambda inp, m: procedures(inp.src.scan("hosp/procedures_icd"), m),
    ),
    EventSource(
        "hcpcsevents",
        "Procedure",
        lambda inp: inp.vocab.code_map(("CPT4", "HCPCS"), "mimiciv_hosp.d_hcpcs.code"),
        lambda inp, m: hcpcs(inp.src.scan("hosp/hcpcsevents"), m),
    ),
    EventSource(
        "drgcodes",
        "Observation",
        lambda inp: inp.vocab.cvb_code_map("mimiciv_hosp.drgcodes.description"),
        lambda inp, m: drgs(inp.src.scan("hosp/drgcodes"), inp.cdm.scan("visit_occurrence"), m),
    ),
    EventSource(
        "labevents",
        "Measurement",
        lambda inp: inp.vocab.cvb_code_map("mimiciv_hosp.d_labitems.itemid"),
        lambda inp, m: labs(
            inp.src.scan("hosp/labevents"),
            inp.cdm.scan("visit_occurrence"),
            inp.cdm.scan("visit_detail"),
            m,
            inp.vocab.unit_map(),
        ),
    ),
    EventSource(
        "prescriptions",
        "Drug",
        lambda inp: _all_targets(_drug_maps(inp)),
        lambda inp, m: drug_events.orders(
            _coded_prescriptions(inp),
            inp.cdm.scan("visit_occurrence"),
            inp.cdm.scan("visit_detail"),
            _routes(inp),
        ),
    ),
    EventSource(
        "emar",
        "Drug",
        lambda inp: _all_targets(_drug_maps(inp)),
        lambda inp, m: drug_events.emar_administrations(
            inp.src.scan("hosp/emar"),
            inp.src.scan("hosp/emar_detail"),
            _coded_prescriptions(inp),
            inp.cdm.scan("visit_occurrence"),
            inp.cdm.scan("visit_detail"),
            _routes(inp),
            _drug_maps(inp)["drug_cvb"],
            _drug_maps(inp)["product_cvb"],
        ),
    ),
    EventSource(
        "chartevents",
        "Measurement",
        _icu_items,
        lambda inp, m: chart_events.charted(
            inp.src.scan("icu/chartevents"),
            inp.cdm.scan("visit_occurrence"),
            inp.cdm.scan("visit_detail"),
            inp.src.scan("icu/icustays"),
            m,
            inp.vocab.cvb_code_map("mimiciv_icu.chartevents.value"),
            inp.vocab.unit_map(),
        ),
    ),
    EventSource(
        "outputevents",
        "Measurement",
        lambda inp: inp.vocab.cvb_code_map("mimiciv_icu.outputevents.itemid"),
        lambda inp, m: chart_events.outputs(
            inp.src.scan("icu/outputevents"),
            inp.cdm.scan("visit_occurrence"),
            inp.cdm.scan("visit_detail"),
            inp.src.scan("icu/icustays"),
            m,
            inp.vocab.unit_map(),
        ),
    ),
    EventSource(
        "omr",
        "Measurement",
        lambda inp: omr.omr_map(omr.local_mapping(), inp.vocab.athena("concept")),
        lambda inp, m: omr.omr_events(
            inp.src.scan("hosp/omr"), inp.cdm.scan("visit_occurrence"), m
        ),
    ),
    EventSource(
        "microbiologyevents",
        "Measurement",
        _micro_codes,
        lambda inp, m: microbiology.micro_events(
            inp.src.scan("hosp/microbiologyevents"),
            inp.cdm.scan("visit_occurrence"),
            inp.cdm.scan("visit_detail"),
            inp.vocab.cvb_code_map(f"{MICRO}.test_itemid"),
            inp.vocab.cvb_code_map(f"{MICRO}.org_itemid"),
            inp.vocab.cvb_code_map(f"{MICRO}.ab_itemid"),
            inp.vocab.cvb_code_map(f"{MICRO}.interpretation"),
        ),
    ),
    EventSource(
        "procedureevents",
        "Procedure",
        _icu_table_items("procedureevents"),
        lambda inp, m: chart_events.procedures(
            inp.src.scan("icu/procedureevents"),
            inp.cdm.scan("visit_occurrence"),
            inp.cdm.scan("visit_detail"),
            inp.src.scan("icu/icustays"),
            m,
        ),
    ),
    EventSource(
        "datetimeevents",
        "Procedure",
        _icu_table_items("datetimeevents"),
        lambda inp, m: chart_events.dated(
            inp.src.scan("icu/datetimeevents"),
            inp.cdm.scan("visit_occurrence"),
            inp.cdm.scan("visit_detail"),
            m,
        ),
    ),
    EventSource(
        "ed_diagnosis",
        "Condition",
        _diagnoses_map,
        _ed(
            lambda inp: ed.diagnoses(
                inp.src.scan("ed/diagnosis"), _ed_links(inp), _diagnoses_map(inp)
            )
        ),
    ),
    EventSource(
        "ed_triage",
        "Measurement",
        _ed_vitals_map,
        _ed(lambda inp: ed.triage(inp.src.scan("ed/triage"), _ed_links(inp), _ed_vitals_map(inp))),
    ),
    EventSource(
        "ed_vitalsign",
        "Measurement",
        _ed_vitals_map,
        _ed(
            lambda inp: ed.vital_signs(
                inp.src.scan("ed/vitalsign"),
                _ed_links(inp),
                inp.cdm.scan("visit_occurrence"),
                inp.cdm.scan("visit_detail"),
                _ed_vitals_map(inp),
            )
        ),
    ),
    EventSource(
        "ed_medrecon",
        "Drug",
        _ed_drug_map,
        _ed(
            lambda inp: ed.medrecon(
                inp.src.scan("ed/medrecon"),
                _ed_links(inp),
                inp.cdm.scan("visit_occurrence"),
                inp.cdm.scan("visit_detail"),
                inp.vocab.code_map(("NDC",)),
                _gsn_map(inp),
            )
        ),
    ),
    EventSource(
        "ed_pyxis",
        "Drug",
        _ed_drug_map,
        _ed(
            lambda inp: ed.pyxis(
                inp.src.scan("ed/pyxis"),
                _ed_links(inp),
                inp.cdm.scan("visit_occurrence"),
                inp.cdm.scan("visit_detail"),
                _gsn_map(inp),
            )
        ),
    ),
    EventSource(
        "inputevents",
        "Drug",
        lambda inp: inp.vocab.cvb_code_map("mimiciv_icu.inputevents.itemid"),
        lambda inp, m: drug_events.icu_inputs(
            inp.src.scan("icu/inputevents"),
            inp.cdm.scan("visit_occurrence"),
            inp.cdm.scan("visit_detail"),
            inp.src.scan("icu/icustays"),
            m,
        ),
    ),
)


def all_events(inp: Inputs) -> pl.LazyFrame:
    """Every source's events, computed (not read from materialized events)."""
    return pl.concat([s.build(inp, s.mapping(inp)) for s in SOURCES])


# Materialized events: <cdm>/.events/<source>/bucket-<k>/domain_id=<domain>/*.parquet
EVENTS_DIR = ".events"


def _bucket_dir(inp: Inputs, source: str) -> Path:
    k = inp.cdm.bucket[0] if inp.cdm.bucket else 0
    return inp.cdm.root / EVENTS_DIR / source / f"bucket-{k:04d}"


def materialize(inp: Inputs) -> None:
    """Compute every source's events for inp's person bucket once, partitioned by domain, so each
    event table reads only its own domain instead of recomputing all sources (chartevents alone
    has hundreds of millions of rows)."""
    for source in SOURCES:
        target = _bucket_dir(inp, source.name)
        shutil.rmtree(target, ignore_errors=True)
        source.build(inp, source.mapping(inp)).sink_parquet(
            pl.PartitionBy(target, key="domain_id", include_key=True), mkdir=True
        )


def is_materialized(inp: Inputs) -> bool:
    return all(_bucket_dir(inp, s.name).is_dir() for s in SOURCES)


def domain_events(inp: Inputs, domain: str) -> pl.LazyFrame:
    """Events of one domain: read from the materialized events of inp's bucket when they exist,
    else computed from the sources that can produce the domain."""
    if is_materialized(inp):
        files = [
            f
            for s in SOURCES
            for f in sorted(
                (_bucket_dir(inp, s.name) / f"domain_id={quote(domain)}").glob("*.parquet")
            )
        ]
        if not files:
            return pl.LazyFrame(schema=EVENT_SCHEMA)
        return pl.scan_parquet(files, schema=EVENT_SCHEMA)
    parts = [s.build(inp, s.mapping(inp)) for s in SOURCES if domain in s.domains(inp)]
    if not parts:
        return pl.LazyFrame(schema=EVENT_SCHEMA)
    return pl.concat(parts).filter(pl.col("domain_id") == domain)


# Events of domains without a built table, kept after a run for accounting (accounting.py)
UNROUTED_FILE = ".unrouted.parquet"


def unrouted_events(cdm_root: Path, built: set[str]) -> pl.LazyFrame:
    """Materialized events of domains whose CDM table is not (yet) built."""
    files = sorted((Path(cdm_root) / EVENTS_DIR).glob("*/bucket-*/domain_id=*/*.parquet"))
    if not files:
        return pl.LazyFrame(schema=EVENT_SCHEMA)
    table = pl.col("domain_id").replace_strict(DOMAIN_TABLE, default=None)
    return pl.scan_parquet(files, schema=EVENT_SCHEMA).filter(table.is_null() | ~table.is_in(built))


def unrouted(cdm_root: Path, built: set[str]) -> pl.DataFrame:
    """Materialized event counts per domain whose CDM table is not (yet) built."""
    return unrouted_events(cdm_root, built).group_by("domain_id").len().sort("domain_id").collect()
