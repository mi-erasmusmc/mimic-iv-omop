# Evaluation plan

DQD and Achilles check that the CDM is well formed. They do not check that it says the same
thing as MIMIC-IV. The evaluation asks three questions, each answered by comparing analyses on
the source with the same analyses on the CDM:

1. **Conservation:** is every source record accounted for? (E1)
2. **Phenotypes:** do clinical definitions find the same patients, on the same dates, with the
   same values? (E2)
3. **Prediction:** do models built from the CDM perform as well as models built from the source?
   (E3)

Each comparison holds the definition fixed and changes only the data it runs on, so that a
difference is caused by the ETL and not by two definitions of the same thing. Where a
comparison cannot do that (E2c), it is reported as context, not as a test of the ETL.

The observation period strategy, THEMIS or encounter-based
([review R1](review.md#r1-observation-periods-themis-ehr-convention-vs-encounter-based)), is
evaluated as a factor wherever it can change a result (see [below](#observation-periods)).

**Prior work** (literature search 2026-10-30). No published study runs the same phenotype or
prediction analysis on MIMIC-III or MIMIC-IV and on its OMOP conversion and compares the answers;
this evaluation would be the first.
- **MIMIC ETLs.** Paris et al. ([JMIR Med Inform 2021](https://medinform.jmir.org/2021/12/e30970),
  MIMIC-III to CDM 5.3) evaluated by unit tests, Achilles, mapping coverage (78% of source
  concepts) and a datathon, and name a source-versus-CDM comparison "an ideal but complex
  validation method" they did not carry out. The OHDSI/MIMIC ETL (CDM 5.3.1, BigQuery) has unit
  tests and QA queries; its planned acceptance tests by replicating studies were reported as
  ongoing ([OHDSI 2021](https://www.ohdsi.org/wp-content/uploads/2021/09/46-abstract-Kallfelz.pdf))
  and never published. The closest current ETL, CogStack's dbt_mimic_omop (MIMIC-IV 3.1 to CDM 5.4
  on DuckDB, [BioNLP 2026](https://aclanthology.org/2026.bionlp-1.80/)), reports mapping rates and
  row counts only.
- **Source-versus-CDM replication elsewhere**, the template for this design: CPRD
  ([Matcho et al. 2014](https://pubmed.ncbi.nlm.nih.gov/25187016/): prevalences and a replicated
  case-control study), IMRD-UK ([Candore et al. 2020](https://pmc.ncbi.nlm.nih.gov/articles/PMC7158210):
  discrepancies fed back to the ETL until the rerun was almost identical), CALIBER
  ([Papez et al. 2021](https://academic.oup.com/jamiaopen/article/4/3/ooab001/6127557): comorbidity
  prevalences, COPD off by 3.8 points from broad codes mapping to one concept), eMERGE phenotypes
  ported to OMOP ([Hripcsak et al. 2019](https://pubmed.ncbi.nlm.nih.gov/31325501/): agreement
  43-100%, labs hardest), Nordic and Portuguese registries
  ([Aborageh et al. 2026](https://www.medrxiv.org/content/10.64898/2026.07.06.26357353v1.full):
  deviations within 5-10%, after iterative benchmarking). None compares prediction models.
- **Prediction on OMOP versus native data:** no study trains the same model on both versions of
  one database. PyHealth has separate MIMIC-IV and OMOP task classes, but defines them separately.

## Prerequisites

| | What | Why |
|---|---|---|
| P1 | Observation period strategy as a run option (`--observation-periods themis\|encounter`, recorded in `metadata`), and a second full CDM build | to compare the two strategies on the same data |
| P2 | `icu.procedureevents` and `hosp.microbiologyevents` in the ETL ([roadmap](../README.md#roadmap) item 1); done | ventilation, CRRT and lines come from procedureevents, and sepsis-3's suspected infection needs culture times. Without them those concepts can only fail on the CDM. The source MEDS (E3) also uses procedureevents |
| P3 | MIMIC-IV 3.1 in DuckDB (views over the converted parquet, schemas `mimiciv_hosp` and `mimiciv_icu`), with mimic-code's [`concepts_duckdb`](https://github.com/MIT-LCP/mimic-code/tree/main/mimic-iv/concepts_duckdb) at a pinned commit (done: `mimic-eval fetch-mimic-code`, commit `303d26c6`) | source-side reference values for E2a |
| P4 | The CDM converted to MEDS with the evaluation's own converter (see [E3](#inputs): `meds_etl` and OMOP_MEDS accept CDM 5.3/5.4 only, and code events or ICU stays in ways that would confound the comparison) | the CDM-side input for E3 |

## E1. Conservation

`mimic-omop account --source <parquet> --cdm <cdm> --out <dir>` (`src/mimic_omop/accounting.py`)
compares every coded source table with the CDM, per source code as written to
`*_source_value`:

- **Row accounting:** every source row becomes CDM records (in whichever table its domain
  routes it to), is routed to a domain whose table is not built yet (kept by each run in
  `<cdm>/.unrouted.parquet`), or is left out for a documented reason (eMAR actions other than
  administrations; ICU inputs marked rewritten). CDM records are traced to their source row
  through the event id.
- **Per-code counts:** for each source code (ICD code, lab and chart item id, drug name, ...),
  rows kept in the source and rows found in the CDM. These must be equal; `missing` is their
  difference.
- **Mapping coverage and visit linkage per source:** the share of rows and of distinct codes
  with a standard concept, the share of rows with a visit, and the most frequent unmapped codes
  (also the input to the CVB feedback list, [review R26](review.md#r26-feedback-for-cvb)).

Outputs: `codes.parquet` (per source and code), `summary.csv` (per source), `unmapped.csv`
(top 20 unmapped codes per source). The demo test runs the same check.

**Pass:** no code with `missing` other than 0.

## E2. Phenotypes

### E2a. mimic-code concepts, same logic on both sides

mimic-code's SQL runs unchanged on both sides (`evaluation/`, `mimic-eval e2a`): once on views
over the converted source parquet, and once on MIMIC-shaped tables rebuilt from the CDM
(`evaluation/src/mimic_eval/cdm_tables.py`), so the definition is literally the same code. The
CDM-side tables use only what the CDM records:

- `admissions`: the admission visits (9201, 262, 581385); `patients`: year of birth as
  `anchor_year` with `anchor_age` 0, so mimic-code's age is the CDM's age;
- `diagnoses_icd`: records of any event table whose source concept is an ICD-9-CM or ICD-10-CM
  code (a second run uses `condition_occurrence` only);
- `labevents`, `chartevents`: measurement records selected by standard concept and unit. Each
  mimic-code item is translated once to the (concept, unit) pairs the CDM gives its records, and
  every record with such a pair, from any source item, is read back as that item. Items that
  share a pair cannot be told apart; records of other items with the pair are included, as an
  analyst using the concept would include them. MIMIC's lab `specimen_id`, which the CDM does
  not keep, becomes one id per person and time;
- `icustays` is taken from the source, so that both sides are compared on the same stays and
  windows; a chart record belongs to the ICU stay that its `visit_detail_id` (the care unit stay
  the CDM attaches it to) is part of.

For Charlson by standard concept, mimic-code's charlson.sql is run on every ICD code in Athena
to find each code's components; a component's concept set is then built from its codes'
'Maps to' (and 'Maps to value') targets in three ways (mapped, specific to the component, with
descendants; `evaluation/src/mimic_eval/charlson.py`), and the CDM's admissions with a concept in
the set get a representative code of the component, so that the same SQL computes the score.
Each concept was chosen for what it tests.

| Concept (mimic-code) | Source input | What the CDM side tests | Compared per |
|---|---|---|---|
| Charlson index (`comorbidity/charlson`) | `diagnoses_icd` code prefixes | (a) via `condition_source_value`: conservation and visit linkage of diagnoses; (b) via standard concepts and their descendants: whether the mapping keeps the meaning | admission: 17 components and the score |
| KDIGO creatinine stage (`organfailure/kdigo_creatinine`, `kdigo_stages`) | `labevents` creatinine, 48-hour and 7-day baselines | measurement concepts, values, units and times | ICU stay: maximum stage, and each time point |
| KDIGO urine output stage (`kdigo_uo`) | `outputevents`, weight | measurements from outputevents, weight from chartevents and omr | ICU stay |
| First-day labs and vital signs (`firstday/first_day_lab`, `first_day_vitalsign`) | `labevents`, `chartevents` | values after unit mapping; this exposes unit problems such as the MCHC and RDW-SD ones DQD found | ICU stay: minimum and maximum per variable |
| SOFA and sepsis-3 (`score/sofa`, `sepsis/*`) | chart, lab, input events (vasopressor rates), antibiotics, cultures | needs P2. The CDM has no standard field for infusion rates, so the cardiovascular component is expected to lose information. The evaluation measures how much | ICU stay |
| Ventilation (`treatment/ventilation`) | `chartevents` oxygen delivery, `procedureevents` | needs P2 | ICU stay: ventilation periods |

**Measures:** kappa and exact agreement for categories; for values, the share identical and the
size of the differences.

**Pass:** Charlson via source values agrees on 100% of admissions. Every other disagreement is
traced to a mapping, unit or timing choice and listed, either as an ETL fix or as a review item.

### E2b. OHDSI phenotypes on the CDM and on the source

**Question.** Does a published OMOP cohort definition find the same people, on the same dates, in
the CDM as the same definition applied to MIMIC-IV? A difference then measures the ETL (domain
routing, visit linkage, dates, observation periods), not the quality of the phenotype.

**Cohorts** (PhenotypeLibrary v3.37.0, pinned; ids checked against the library). Their logic was
inspected: nine are one diagnosis concept set, some with an inpatient or ER visit requirement or
an era window, and a fixed-offset exit; none requires prior observation time.

| Id | Cohort | Logic | Why |
|---|---|---|---|
| 362 | Acute kidney injury | diagnosis or dialysis procedure; 4 exclusion rules with 365-day lookbacks (ESKD, chronic dialysis, transplant) | the only peer-review accepted cohort of the set; procedures and lookbacks |
| 411 | Sepsis or septic shock | diagnosis | E2c against sepsis-3 |
| 71 | Acute myocardial infarction with inpatient admission | diagnosis + inpatient or ER visit | visit linkage |
| 75 | Ischemic stroke with inpatient admission | diagnosis + visit | visit linkage |
| 77 | GI bleeding with inpatient admission | diagnosis + visit | visit linkage |
| 938 | Hospitalization with heart failure | visit entry with a correlated diagnosis, era 7 days | visit-indexed (934, in the earlier list, is not an inpatient definition) |
| 405 | Atrial fibrillation or flutter | diagnosis | frequent; also an ICU chart finding (rhythm) |
| 957 | Type 2 diabetes mellitus | first diagnosis with a second diagnosis, or two drugs, or two HbA1c > 6.5% | the one cohort with drug and lab criteria: exercises drug mapping and lab units |
| 1005 | Venous thromboembolism | diagnosis, episodes 180 days apart | era logic |
| 359, 95 | Acute respiratory failure, delirium | diagnosis | ICU-relevant; delirium is also charted (CAM-ICU) |

All but 362 are "pending peer review"; as before, that does not matter for a test of the data.

**Method: one evaluator, two inputs, plus the official engine.**
1. **Official:** CohortGenerator on the DuckDB CDM (Circe SQL), on both CDM builds (THEMIS and
   encounter periods).
2. **Evaluator on the CDM:** a small Polars implementation of the subset of Circe these cohorts
   use (condition, procedure, drug, measurement and visit criteria with concept sets; occurrence
   counts in windows relative to the index; visit overlap; date-offset exit; era collapse; first
   or all events), run on the CDM. It must reproduce step 1 exactly, which validates it
   (allowing for observation period restrictions, which it applies the same way).
3. **Evaluator on the source:** the same code on MIMIC-IV events. Each concept set is resolved to
   standard concepts (with descendants and exclusions, as Circe does), then to the source codes
   whose 'Maps to' targets are in it: ICD-9/10-CM and procedure codes through Athena, MIMIC's lab
   items and drug names through CVB, exactly as the ETL maps them. Events come from the source
   tables (`diagnoses_icd` dated as the CDM dates them, `procedures_icd`, `labevents`,
   `prescriptions`/`emar`), visits from `admissions` and ED stays. MIMIC has no observation
   periods; the source side uses none, and the effect of the CDM's is reported.

Steps 2 and 3 share code and concept sets, so they differ only in their input. Step 3 does use
the same vocabularies as the ETL: it tests the ETL's implementation (routing, dates, visits,
lost records), not the vocabulary. The vocabulary's meaning is tested separately:

4. **Independent native definitions** (context): for the cohorts with a published ICD-based
   definition (mimic-code's Charlson components and sepsis-3, AHRQ CCSR categories), the native
   ICD definition on the source against the PhenotypeLibrary cohort on the CDM. As in Papez et
   al. and Hripcsak et al., this measures what an OMOP user gets compared with a MIMIC user,
   vocabulary included.

**Measures**, per cohort and per CDM build:
- persons and entries on each side, and the ratio;
- person-level agreement: Jaccard, Cohen's kappa and PABAK (low prevalence), sensitivity and
  PPV of the CDM cohort with the source cohort as reference;
- entry-level agreement: entries matched by person and index date within 0 and within 1 day;
  for matched entries, the distribution of index-date and end-date offsets;
- for every discordant entry, its cause, traced by source code: routed to another domain,
  outside an observation period, different visit, different date, unmapped code;
- CohortDiagnostics on the CDM (index event breakdown, visit context, incidence) to name the codes
  behind a difference.

**Pass:** steps 1 and 2 agree exactly. Between steps 2 and 3, every discordant entry is
explained, and all except those excluded by observation periods are ETL decisions documented in
the review (domain routing, R12; visit dates, R13). Tolerance for the explained part is not set:
it is the result.

**Effort:** medium. R packages on the server (CohortGenerator, CirceR, PhenotypeLibrary,
CohortDiagnostics from GitHub); the evaluator is the largest part, a few hundred lines.

### E2c. Clinical cross-check (context, not an ETL test)

The code-based cohorts are compared with mimic-code's physiological definitions on the same
patients: sepsis (411) with sepsis-3, and AKI (362) with the KDIGO stages. Diagnosis codes are
known to undercount both. This describes what a code-based phenotype misses in ICU data, useful to
anyone using this CDM, but it is not a measure of the ETL. It needs sepsis-3 on the source side
(E2a extension: SOFA, suspected infection), which mimic-code provides.

## E3. Prediction

**Question.** Do models trained on the CDM perform as well as the same models trained on MIMIC-IV,
for the same patients, prediction times and labels? A difference measures information the ETL
keeps, loses, adds or misdates.

### Inputs

- **Source side:** MIMIC-IV 3.1 in MEDS. A full build exists (MIMIC_IV_MEDS with MEDS_transforms
  0.6.1, 364,627 subjects, 723 M events, subject splits 291,702 / 36,463 / 36,462), with labels
  for the MEDS-DEV tasks. It covers admissions, transfers, ICU stays, diagnoses, procedures,
  HCPCS, DRGs, labs, eMAR, pharmacy, omr and the ICU event tables; not microbiology,
  `datetimeevents`, `prescriptions` or MIMIC-IV-ED. It was rebuilt on the server with the
  MIMIC-IV-MEDS version MEDS-DEV pins (0.1.0, MEDS_transforms 0.6.2; 76 minutes, 195 GB peak
  memory), from the same raw files as the CDM and without PhysioNet credentials (`do_download=False`,
  with mimic-code v2.4.0's concept maps staged next to the raw files): 877.8 M events, the same
  364,627 subjects and the same splits as the earlier build, with the code format the current
  MEDS-DEV predicates expect (`LAB//RESULT//50912//mg/dL`).
- **CDM side:** the CDM converted to MEDS (P4). The available converters cannot be used as they are:
  - `meds_etl` (0.3.11) and its successor OMOP_MEDS (0.3.0) accept CDM 5.3 and 5.4 only (5.5 is
    additive, so `--omop_version 5.4` probably works);
  - `meds_etl` codes each event by its **source** concept when there is one, so the MEDS would
    hold ICD codes and CVB's MIMIC4 concepts, not the standard concepts; OMOP_MEDS uses standard
    concepts;
  - neither emits ICU stays as events (needed for the ICU tasks), and `meds_etl` emits no visit
    end when `discharged_to_concept_id` is 0, which most visits are.

  **Decision proposed:** a small OMOP to MEDS converter in `evaluation/` (Polars, reading the
  CDM parquet), with each choice explicit and documented: code by standard concept (main) or by
  source concept (variant, as `meds_etl`); values, units and times from the CDM fields; ICU stay
  start and end events from `visit_detail` (consecutive ICU care unit stays merged, as MIMIC's
  `icustays` does: 118,077 ICU care unit stays against 94,458 ICU stays); visit start and end
  events for every visit. `meds_etl` is run once as a cross-check of the converter.
- **Diagnosis timing.** The source MEDS dates diagnoses and DRGs at discharge; the CDM dates them
  at the visit start (OHDSI convention). Taken as they are, the CDM-side features of a 24-hour task
  would include the admission's own discharge diagnoses: a leak that makes the CDM look better
  than the source. The converter therefore places diagnosis billing records at their visit's end
  (main): type 32821 EHR billing record with an ICD-9-CM or ICD-10-CM diagnosis code as source
  concept, in whichever table the code's domain put it; a rule on CDM fields only. Procedure codes
  (ICD procedures, HCPCS) are also typed billing but carry their own dates, as in the source MEDS,
  and DRGs are already dated at discharge, so neither moves. The CDM as it is runs as a variant:
  the size of the leak is itself a finding for any OMOP user predicting within an admission.
- **Shared:** `person_id` is `subject_id`, so both sides use the same subjects, the same splits and
  the same prediction times.

### Tasks

The MEDS-DEV tasks at a pinned commit. Upstream now has eight for MIMIC-IV:

- in-ICU mortality from the first 24 hours;
- seven abnormal-lab tasks from the first 24 hours of the admission: elevated creatinine,
  hyponatremia, metabolic acidosis, anemia, leukocytosis, thrombocytopenia, hypotension (most
  sensitive to lab and unit mapping).

30-day readmission was removed upstream as unsuitable for MIMIC (no follow-up outside the
hospital). That does not affect a comparison of two versions of the same data, so it stays, at
the commit the existing labels were made with, marked as a comparison task.

One task is added, written in ACES the same way: in-hospital mortality from the first 24 hours of
the admission, the task most exposed to the diagnosis timing above.

MEDS-DEV's published MIMIC-IV results (MEDS-Tab "tiny", AUROC 0.57-0.72; CEHR-BERT 0.83 for
ICU mortality) are smoke tests of unknown data versions, not baselines; this evaluation produces
its own source-side baselines.

### E3a. Same labels, different features (main comparison)

Labels and prediction times come from the source. One model per task and side
(`evaluation/meds/e3.py`; the first two runs used MEDS-Tab 0.2.0 and `e3a.py`):
- **Features:** MEDS-Tab's full tabularization (windows 1 day, 7 days, 30 days and the whole
  history; code counts and value aggregates), computed by `featurize.py`.
- **LightGBM**, with hyperparameters tuned once per task on the source and frozen, identical on
  both sides.
- **Cyclops L1 logistic regression** on the same features as a second, simpler learner, also
  with quintile indicators for the values. Its prior variance is frozen for routine runs and
  chosen by 3-fold cross-validation for final runs.
- **XGBoost** with MEDS-Tab's search space, in final runs only.

Sequence models such as CEHR-BERT are out of scope: two learners on the same features are enough
to compare the data.

Two model diagnostics run beside the comparison:
- `--decompose`: the LR on code indicators and on counts, and LightGBM with stumps, to locate the
  gap between the LR and the trees;
- `--ladder` (source side): from inputs shaped like FeatureExtraction's (each code present or not
  in 30 days and in all history, age groups; not its default covariate set) to the full features,
  one addition at a time, to show what the extra featurization is worth.

Variants, each changing one thing:

| Variant | CDM-side input | Separates |
|---|---|---|
| main | standard concepts, diagnoses at visit end | |
| as it is | diagnoses at visit start | the diagnosis-timing leak |
| source codes | coded by source concept | mapping (standard concepts) from conservation |
| input-matched | the CDM restricted to the source tables the source MEDS uses | information from tables the source MEDS leaves out (microbiology, ICU dates, ED, orders) from information the ETL loses |

**Measures** on the held-out split:
- AUROC, AUPRC, calibration (intercept, slope, ICI);
- the paired difference CDM minus source with a 2,000-replicate bootstrap over subjects (DeLong
  as a check);
- for "same answer", an equivalence margin of ±0.01 AUROC fixed before the runs (two
  one-sided tests);
- agreement of predicted risks for the same prediction times (correlation, top-decile overlap).

**Pass:** the main variant within the margin for every task. Any difference outside it is traced
through the variants and feature importances to a cause.

### E3b. Labels from each side

The same tasks get OMOP predicates, written once for the CDM MEDS:
- death from `death`;
- ICU stays from the merged ICU `visit_detail` events;
- admissions from `visit_occurrence`;
- labs by LOINC concept and UCUM unit, with the same thresholds.

ACES has no concept hierarchy, so concept sets are expanded to explicit codes. ACES's handling of
value thresholds with regex codes is tested first, because its sources disagree.

The comparison reports label agreement per subject and prediction time (kappa, and the causes of
each disagreement), then the E3a models evaluated against the CDM's own labels. This tests
whether the tasks can be defined from the CDM at all.

### E3c. OHDSI-native prediction

PatientLevelPrediction 6.7 (LASSO and gradient boosting) on the DuckDB CDM, with
FeatureExtraction's covariates.
- **Tasks**, at day level, since OHDSI tools work in days:
  - in-ICU mortality: target the ICU stays lasting at least one day, index the day after ICU
    admission, outcome death until ICU discharge;
  - in-hospital mortality: index the day after admission;
  - 30-day readmission.

  These are close to, but not the same as, the E3a tasks.
- **Covariates:** up to the index day. The admission's own billing diagnoses are left out of the
  short-term window, for the leak described above.
- **Comparisons:**
  - with the same task built from the CDM MEDS in E3a, to see whether the OHDSI pipeline reaches
    similar performance;
  - between the two CDM builds, because PLP's time at risk, washout and censoring depend on the
    observation periods. Under encounter periods, nobody is observed after discharge, so
    readmission cannot be measured; quantifying that is the point.

  This is a test of fitness for OHDSI use, not of conservation: there is no source-side PLP.

## Observation periods

The two strategies differ in the observation they record after discharge and before admission.
Expected effects, to be measured:

| Analysis | Effect of encounter-based periods |
|---|---|
| E2b cohorts | none for entry events inside visits; AKI's 365-day exclusions see less history; records outside visits (ICU dates before admission, R34) are dropped |
| E3a, E3b | none: MEDS and ACES do not use observation periods |
| E3c | 30-day readmission: nobody is observed after discharge, so with `requireTimeAtRisk` every patient is excluded and without it every patient is censored; mortality after discharge is likewise unobservable |
| DQD, Achilles | events after a period's end; plausibility checks on time in observation |

The THEMIS 60-day padding and 548-day persistence window are the opposite risk: time counted as
observed when MIMIC cannot see the patient. Events elsewhere are then missed, and incidence is
underestimated. E2b and E3c report both builds side by side.

## Organisation

- **Code:** in `evaluation/`, its own uv project:
  - `mimic_eval/cohorts.py`: the cohort evaluator;
  - `mimic_eval/omop_meds.py`: the converter;
  - `evaluation/R/` for CohortGenerator, CohortDiagnostics and PLP;
  - `evaluation/tasks/` for the ACES configs and the OMOP predicates.

  MEDS-Tab, XGBoost and the R packages do not become dependencies of the ETL.
- **Compute:**
  - the full-data runs used one server (192 cores, 246 GB of memory);
  - R packages go into a library of the project's own, not the system's;
  - the source MEDS and labels are kept under the same access restrictions as MIMIC-IV.
- **Reproducibility:** every input is pinned and recorded with the results: MIMIC-IV 3.1, the
  Athena release, CVB, mimic-code, PhenotypeLibrary, MEDS-DEV, MIMIC-IV-MEDS, MEDS-Tab and ACES.
- **Results:** in `data/eval/`, and written up in `evaluation-results.md` as each part completes.
  Discrepancies found are fixed in the ETL or recorded in the review, and the comparison is
  rerun, as in Candore et al.

## Order

1. P1 and a second CDM build. Small. *Done.*
2. E1 on the full data. Small; extends existing tests. *Done.*
3. E2a: Charlson, KDIGO creatinine, first-day labs and vital signs. Medium; most likely to find
   mapping problems early. *Done*
   ([results](evaluation-results.md#e2a-mimic-code-concepts-on-the-source-and-on-the-cdm)),
   except KDIGO urine output, which needs mimic-code's weight and hourly stay tables.
4. **Setup:**
   - R packages into the project library;
   - the source MEDS and labels copied to the server;
   - MEDS-DEV, MEDS-Tab and ACES pinned in `evaluation/`;
   - a check that CohortGenerator runs one cohort on the DuckDB CDM.

   Small.
5. **E2b steps 1-3.** Medium.
   - Start with AKI (362), AMI (71) and T2DM (957), which between them use every criterion type,
     then the rest.
   - The cohort evaluator is validated against CohortGenerator before it is run on the source.
6. **P4 and E3a.** Medium-large.
   - The converter and its tests come first. The tests check event counts per table against the
     CDM, ICU stays against `icustays`, and diagnosis timing.
   - Then MEDS-Tab on the source and on the CDM, starting with in-ICU mortality and two lab
     tasks, then the rest and the variants.
7. **E3b.** Medium.
8. **E3c.** Medium. On both CDM builds.
9. **E2a extension and E2c:** sepsis-3, SOFA and ventilation on both sides, then the clinical
   cross-check. Medium.
10. **E2b step 4**, independent native definitions. Small, once 5 is done.
