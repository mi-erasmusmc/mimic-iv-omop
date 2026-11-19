# mimic-iv-omop

An ETL from [MIMIC-IV](https://physionet.org/content/mimiciv/) 3.1 and
[MIMIC-IV-ED](https://physionet.org/content/mimic-iv-ed/) 2.2 to the
[OMOP CDM 5.5](https://ohdsi.github.io/CommonDataModel/cdm55.html). It is written in Python with
[Polars](https://pola.rs), writes plain parquet, and builds the full MIMIC-IV in under four
minutes. It is checked against the source, not only for conformance: every coded source row is
accounted for, and clinical concepts, OHDSI phenotypes and prediction models were compared on the
source and on the CDM, with every difference traced to its cause.

## Why use it

- **Validated against the source.** No other MIMIC ETL has published a comparison of analyses on
  the source and on its CDM ([plan](docs/evaluation.md), [results](docs/evaluation-results.md)):
  - **Conservation:** all 720,194,207 coded rows of 19 source tables are in the CDM, none
    missing, and no source code has a different count. Each record's id names its source row,
    which makes the check exact.
  - **Same SQL on both sides:** mimic-code's concept queries run unchanged on the source and on
    MIMIC-shaped tables rebuilt from the CDM.
    - Queried by source code across all event tables, all 17 Charlson components agree on every
      admission.
    - The KDIGO creatinine stage agrees on 99.999% of ICU stays.
    - 71 of 86 first-day lab variables are identical for at least 99.9% of stays, and 23 of 24
      vital signs for at least 99.0%.
    - No value is ever changed: every difference was traced to other records that share the
      same concept.
  - **OHDSI phenotypes:** 11 PhenotypeLibrary cohorts were built with CohortGenerator on the CDM
    and with the same logic on the source. Six agree (Jaccard 1.000, at most two persons
    differ). Every difference in the other five (Jaccard 0.913–0.989) was traced to a documented
    ETL decision, such as typing observation stays as outpatient visits, or to observation
    periods.
  - **Prediction:** the same labels were fit with LightGBM and an L1 logistic regression on
    features from the source and from the CDM, for 10 tasks. AUROC differences are within ±0.005
    for 6 of 10 tasks with LightGBM and 7 of 10 with the regression. The lab tasks' gaps come
    from OMOP's single time per lab result. Half of readmission's gap comes from the CDM's later
    discharge time, and the rest is still being investigated.
- **Complete.**
  - Out-of-hospital deaths (`patients.dod`).
  - Prescriptions, eMAR administrations and ICU inputs.
  - Labs, charted data, outputs and outpatient measurements (`omr`).
  - Microbiology, as specimens and measurements linked by `fact_relationship`.
  - ICU procedures and dates.
  - MIMIC-IV-ED's diagnoses, triage, vital signs, medicine reconciliation and dispensing.
  - Insurance as `payer_plan_period`; episodes; condition and drug eras.
- **Fast, local, plain files.** No database server or cloud account is needed.
  - The full MIMIC-IV 3.1 with ED builds in under 4 minutes and about 6 GB of memory on a
    192-core server. It has also been built on a laptop.
  - The output is parquet, readable by DuckDB, Spark, R or pandas.
  - `export-duckdb` writes a DuckDB database with the CDM's 32-bit integer ids, used with
    Achilles, DQD, CohortGenerator and FeatureExtraction.
- **Documented for review.**
  - One page per CDM table maps every field ([`docs/etl/`](docs/etl/)), and a test keeps these
    pages complete.
  - Every choice the CDM and the OHDSI THEMIS conventions leave open is in
    [`docs/review.md`](docs/review.md), with the alternatives and their numbers.
  - Mappings come from Athena and the Tufts [CVB](https://github.com/TuftsCTSI/CVB/tree/main/MIMIC)
    MIMIC vocabulary, pinned to a commit, not from mapping files maintained here.
- **Tested and reproducible.**
  - Each CDM table is a pure function, unit tested and property tested
    ([Hypothesis](https://hypothesis.readthedocs.io)).
  - CI builds the MIMIC-IV demo end to end.
  - Every build is validated against the CDM 5.5 specification before it replaces the previous
    output: types, required fields, unique keys, foreign keys, concept validity.
  - Ids are derived from source keys, so the same input always gives the same output.
  - **DQD** passes 2,350 of 2,409 checks (97.6%) on the full data, and every failure is explained
    ([`docs/dqd.md`](docs/dqd.md)).

## Compared with other MIMIC-IV ETLs

As of November 2026, from each project's repository and papers. Corrections are welcome.

| | this ETL | [OHDSI/MIMIC](https://github.com/OHDSI/MIMIC) | [CogStack dbt_mimic_omop](https://github.com/CogStack/dbt_mimic_omop) |
|---|---|---|---|
| CDM version | 5.5 | 5.3.1 (`main`, paused); 5.4.2 on the `dev` branch | 5.4 |
| MIMIC-IV | 3.1, with ED 2.2 | 2.2 last documented | 3.1, with Note 2.2 and CXR-JPG 2.1.0 |
| Runs on | Python (Polars), local; under 4 min | Google BigQuery (needs a GCP project) | dbt with DuckDB, local; under 3 h on a laptop |
| Mappings | Athena + Tufts CVB, pinned | Athena + custom CSVs (`main`); CVB on `dev` | Athena + OHDSI/MIMIC's custom CSVs |
| Out-of-hospital deaths | yes | no (in-hospital `deathtime` only) | yes |
| eMAR, ICU inputs, `omr` | yes | no | no |
| MIMIC-IV-ED | yes | no | no |
| Notes and NLP | no | no | yes (195.6M MedCAT annotations) |
| Chest X-ray imaging | no | no | yes (MI-CDM imaging tables) |
| provider | no | no (left empty) | yes (a build failure is reported, issue #3) |
| payer_plan_period, episode | yes | no | no |
| dose_era | no | yes | yes |
| Record ids | from source keys: deterministic, traceable; 32-bit in the DuckDB export | random 64-bit, new each run (`main`); mostly deterministic on `dev` | unsigned 64-bit hashes; nondeterministic builds (an open PR fixes both) |
| Tests | unit and property tests, demo end to end in CI, validation on every build | SQL unit tests and QA scripts; no CI | dbt schema tests; no CI |
| DQD published | full MIMIC-IV 3.1: 97.6% pass | 100-patient demo ([PhysioNet](https://physionet.org/content/mimic-iv-demo-omop/)) | no |
| Compared with the source | conservation, mimic-code concepts, OHDSI phenotypes, prediction | planned (user acceptance tests, unpublished) | mapping rates and row counts |
| License | Apache-2.0 | Apache-2.0 | none stated |

OHDSI/MIMIC is the official OHDSI ETL. It runs on PhysioNet's BigQuery copy without a download,
has a published demo dataset, and is adding waveforms. CogStack's ETL is the one to use if you
need notes, NLP annotations or chest X-ray images in OMOP.

## Not (yet) included

- `provider` (MIMIC-IV 3.x has `provider` and `caregiver`), `poe` (order entry, such as code
  status), `ingredientevents`, `dose_era`.
- MIMIC-IV-Note and MIMIC-CXR.
- Lab text results as value concepts; drug quantities from Athena's `drug_strength`.
- Items CVB does not map: 55% of chartevents rows get a standard concept (nursing
  documentation, for example, has no mapping). The rates per source are in
  [E1](docs/evaluation-results.md#e1-conservation).
- OMOP has one time per measurement, so a lab result is dated when its specimen was drawn, not
  when the result was available. Models predicting within an admission should take this into
  account ([E3](docs/evaluation-results.md#third-run-lightgbm-and-cyclops-e3py)).

## Usage

The Python package and its command are called `mimic-omop`.

```sh
uv sync

# open demo data (100 patients) and the pinned CVB mappings
uv run mimic-omop fetch-demo                  # -> data/mimic-iv-demo-2.2
uv run mimic-omop fetch-cvb                   # -> data/vocab

# an unzipped Athena download (athena.ohdsi.org; not redistributable, so not fetched for you)
uv run mimic-omop load-athena ~/data/athena   # -> data/vocab/athena/<table>.parquet

# PhysioNet csv.gz -> typed parquet (streams; works the same for the full dataset)
uv run mimic-omop convert data/mimic-iv-demo-2.2 data/demo-parquet
# optional: MIMIC-IV-ED (a separate PhysioNet module), converted into the same directory (-> ed/)
# uv run mimic-omop convert <unzipped>/mimic-iv-ed-2.2 data/mimic-iv-3.1-parquet

# build the CDM (full data: add --buckets 32)
uv run mimic-omop run --source data/demo-parquet --vocab data/vocab --out data/demo-cdm \
    --source-version 2.2 --source-release-date 2023-01-06 --cdm-holder "<you or your institution>"
# (--observation-periods encounter: encounter-based periods instead of THEMIS; see docs/review.md R1)

# check that every coded source row is accounted for (docs/evaluation.md, E1)
uv run mimic-omop account --source data/demo-parquet --cdm data/demo-cdm --out data/eval/demo/accounting

# DuckDB for OHDSI tools, then Achilles and DQD (R; see docs/dqd.md)
uv sync --extra duckdb
uv run mimic-omop export-duckdb data/demo-cdm data/demo-cdm.duckdb
Rscript scripts/run_dqd_achilles.R data/demo-cdm.duckdb data/dqd

# tests (the end-to-end demo tests need the fetch steps above; tables that need Athena are
# skipped without it, which is how CI runs)
uv run pytest
```

Select at least the vocabularies in `NEEDED_VOCABULARIES` (`vocab.py`) on athena.ohdsi.org;
`load-athena` warns about missing ones. **Include Cancer Modifier:** Athena drops relationships
into vocabularies outside the download, so without it ICD codes for metastases silently lose
their mapping.

The output is one directory per table, `data/demo-cdm/<table>/part-<k>.parquet`:

```sh
duckdb -c "select count(*) from 'data/demo-cdm/person/*.parquet'"
```

The evaluations (mimic-code concepts, OHDSI cohorts, prediction) are separate uv projects in
[`evaluation/`](evaluation/README.md), so the ETL depends on Polars only.

## Design

```
src/mimic_omop/
  source.py      csv.gz -> parquet with rule-based types (no inference); Source.scan("hosp/patients")
  vocab.py       Athena loading, CVB fetch (pinned), code maps (Athena first, CVB fills gaps)
  context.py     Inputs given to builders: source, vocab, and CDM tables built earlier in the run
  concepts.py    the few standard concepts used directly by ETL conventions
  cdm/spec.py    CDM 5.5 tables and fields, generated from the OHDSI field-level CSV
  cdm/schema.py  conform (column order, types, unknown-column check) and lazy validation
  etl/<table>.py one module per CDM table: a pure builder function + a build(inputs) adapter
  etl/events.py  coded source rows -> events with standard concepts, routed by domain
  etl/intervals.py  interval merging (ED stays, observation periods) and assignment of
                 event times to covering intervals (visits, care units), as window functions
  pipeline.py    table registry in dependency order; sinks, validates, swaps in, checks FKs
  export.py      parquet -> DuckDB with the CDM's 32-bit integers, for the OHDSI tools
```

Everything runs on the Polars streaming engine: conversion is `scan_csv` → `sink_parquet`, and
each CDM table is a lazy query sunk to parquet. `--buckets N` builds subjects in N buckets
(`subject_id % N`), one after another, to bound the memory of joins and window functions over
the largest tables. All CDM logic is per person, so the output does not depend on N (a test
checks this). Builders use only operations the streaming engine executes natively: lookups are
checked for uniqueness up front instead of with `join(validate=...)`, and events are assigned to
visits by time with a merged, forward-filled time line instead of an as-of join.

Every table is sunk to a staging directory and validated there before it replaces the previous
output; foreign keys between CDM tables are checked at the end. With Athena loaded, every
concept field is checked too: ids must exist, and fields other than `*_source_concept_id` must
hold valid standard concepts. Converting the source fails on any value that does not parse,
rather than silently writing nulls.

CDM 5.5 only adds optional fields and vocabulary metadata tables to 5.4, so tools that ignore
extra columns read the output as 5.4. Regenerate the spec with
`uv run python scripts/gen_cdm_spec.py`, pinned to the OHDSI/CommonDataModel `v5.5.0` tag.

## Documentation

[`docs/`](docs/README.md) is written for review by someone who knows OMOP:
- one page per CDM table, with a field-level mapping ([`docs/etl/`](docs/etl/));
- the rules shared by all tables ([`docs/general.md`](docs/general.md));
- the [decisions and open questions](docs/review.md), each with the relevant CDM or THEMIS
  convention, the alternatives and their numbers;
- the [evaluation plan](docs/evaluation.md) and [results](docs/evaluation-results.md).

The CDM 5.5 specification and the THEMIS conventions are followed where they say something:
observation periods use the THEMIS EHR rule by default, coded records go to the table of their
standard concept's domain, and a code mapped to several concepts gives one record per concept.

## Roadmap

1. Remaining sources: `provider`, `poe` (code status), lab text results as value concepts, drug
   quantities from `drug_strength`; MIMIC-IV-Note.
2. Evaluation: prediction tasks defined on the CDM itself (E3b), PatientLevelPrediction on the
   CDM (E3c), and the remaining phenotypes ([plan](docs/evaluation.md)).
3. Mapping feedback to CVB, collected in [review R26](docs/review.md#r26-feedback-for-cvb).

## License

Apache-2.0, see [LICENSE](LICENSE). MIMIC-IV itself is credentialed data under the PhysioNet
license: this repository contains code and aggregate results only, never patient data.

## Acknowledgements

Builds on the conventions of the [OHDSI/MIMIC](https://github.com/OHDSI/MIMIC) ETL and uses the
[Tufts CVB](https://github.com/TuftsCTSI/CVB) MIMIC vocabulary for mappings.
