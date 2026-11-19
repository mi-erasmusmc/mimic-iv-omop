# Evaluation results

Results of the [evaluation plan](evaluation.md), on MIMIC-IV 3.1 with the Athena release of
29-AUG-26 and CVB at commit `bf5701cf`. Each section names the command that reproduces it.

## E1. Conservation

`mimic-omop account --source data/mimic-iv-3.1-parquet --cdm data/mimic-iv-3.1-cdm --out
data/eval/mimic-iv-3.1/accounting` (4 s, 3 GB).

**Every coded source row is accounted for:** 720,194,207 rows in 19 source tables (MIMIC-IV-ED
included; triage and ED vital signs counted per value), none missing,
and no source code with a different count in the CDM. Rows left out by design: 9,657,103 eMAR
actions other than administrations (22.6% of eMAR; 'Confirmed' among them since R18), 8,389,549 ICU dates charted again (R34),
the medrecon and Pyxis repeats per drug class or GSN, and the rows of the 76 ED stays of
patients not in MIMIC-IV. 2,892 diagnosis rows (2,872 hospital, 20 ED) map to Episode concepts
first; they are episodes, linked to their condition record ([episode](etl/episode.md)).

| Source | Rows | In the CDM | Codes | Mapped rows | Mapped codes | With a visit |
|---|---:|---:|---:|---:|---:|---:|
| diagnoses_icd | 6,364,488 | 6,364,488 (2,872 as episodes) | 28,562 | 100.0% | 100.0% | 100% |
| procedures_icd | 859,655 | 859,655 | 14,911 | 99.9% | 99.9% | 100% |
| hcpcsevents | 186,074 | 186,074 | 2,366 | 100% | 100% | 100% |
| drgcodes | 761,856 | 761,856 | 1,087 | 40.7% | 58.3% | 100% |
| labevents | 158,374,764 | 158,374,764 | 976 | 93.1% | 87.7% | 100% |
| prescriptions | 20,292,611 | 20,292,611 | 10,924 | 94.6% | 78.6% | 100% |
| emar | 42,808,593 | 33,151,490 administrations | 5,042 | 94.1% | 77.4% | 98.4% |
| inputevents | 10,953,713 | 10,953,713 | 327 | 86.0% | 62.1% | 100% |
| chartevents | 432,997,491 | 432,997,491 | 2,311 | 55.1% | 31.1% | 100% |
| outputevents | 5,359,395 | 5,359,395 | 71 | 92.3% | 91.5% | 100% |
| omr | 7,753,027 | 7,753,027 | 13 | 100.0% | 92.3% | 99.7% |
| microbiologyevents | 3,988,224 | 3,988,224 | 228 | 95.9% | 75.4% | 99.9% |
| procedureevents | 808,706 | 808,706 | 159 | 96.2% | 83.0% | 100% |
| datetimeevents | 9,979,761 | 1,590,212 (one per patient, item and date) | 174 | 43.6% | 32.2% | 100% |
| ed_diagnosis | 899,050 | 898,874 (20 as episodes) | 13,199 | 100.0% | 100.0% | 100.0% |
| ed_triage | 3,687,042 | 3,686,388 | 9 | 100.0% | 100.0% | 100.0% |
| ed_vitalsign | 9,546,362 | 9,544,495 | 8 | 100.0% | 100.0% | 100.0% |
| ed_medrecon | 2,987,342 | 2,598,212 | 18,710 | 95.8% | 59.7% | 100.0% |
| ed_pyxis | 1,586,053 | 1,189,218 | 1,102 | 96.9% | 94.7% | 100.0% |

"Mapped" is a standard concept other than 0; codes are distinct `*_source_value`s. The table is
from the build with CVB's broader concepts used for items without an equivalent ([review
R29](review.md#r29-cvb-items-mapped-only-to-a-broader-concept)), less the 9 CVB relationships
found wrong (general rule 9), and the unitless omr names mapped
([R21](review.md#r21-local-mapping-for-omr)); the first build, without them, had
chartevents 41.7%, inputevents 70.2%, outputevents 89.6%, labevents 93.0% and diagnoses 99.4%
of rows mapped. Row counts and the conservation result are the same for both.

**Found and fixed.** 12 admissions are coded in both ICD-9 and ICD-10 with the same sequence
numbers (232 diagnosis rows, 10 procedure rows). All their codes were in the CDM, but two
source rows shared one event key; the ICD version is now part of the key
([general](general.md#identifiers)).

**Coverage gaps,** from `unmapped.csv`:

- **CVB broad matches** ([review R29](review.md#r29-cvb-items-mapped-only-to-a-broader-concept)):
  CVB related 387 chart items (14.7% of chartevents rows) and 44 ICU inputs (15.8% of
  inputevents rows, among them midazolam, hydromorphone and prophylactic heparin) only to a
  broader standard concept. This was the largest gap; these items are now mapped to that
  concept, except 48 chart items (0.8% of rows) with several unrelated broader concepts.
- **Chart and input items CVB relates only to narrower concepts** (`Subsumes`; 3.0% of
  chartevents, 3.5% of inputevents rows): not mapped, since a narrower concept says more than
  the item.
- **Chart items without any CVB relation** (11.2% of chartevents rows): nursing documentation
  such as safety measures, ectopy type, head of bed, turning
  ([review R22](review.md#r22-unmapped-chart-items)).
- **APR-DRGs** (90% of unmapped DRG rows): Athena's DRG vocabulary has MS-DRGs only.
- **Labs** (6.3% of rows unmapped): items CVB labels junk (3.9% of rows, mostly the serum
  indices L and I) and items CVB does not list (2.0%, among them the serum index H). The three
  serum indices (hemolysis, lipemia, icterus; 1.7% of rows each) have LOINC codes and could be
  mapped ([R26](review.md#r26-feedback-for-cvb)). Specimen hold tubes, listed by CVB without a
  mapping, now get its broader "Blood chemistry" concept.
- **ICD-10-CM place of occurrence** (Y92 codes): no standard concept in Athena; CVB's broader
  "Place of occurrence of injury" concepts are now used (one code, one row, remains).
- **omr** `eGFR` (279 rows): the equation is not recorded
  ([review R21](review.md#r21-local-mapping-for-omr)); the unitless `BMI`, `Weight` and `Height`
  are now mapped.

The CDM's event ids identify their source rows, which is what makes this check exact: a
record's id names its source table, row and the position of its concept among the row's
mappings.

## E2a. mimic-code concepts on the source and on the CDM

`cd evaluation && uv run mimic-eval e2a --source ../data/mimic-iv-3.1-parquet --cdm
../data/mimic-iv-3.1-cdm --vocab ../data/vocab --mimic-code ../data/eval/vendor/mimic-code --out
../data/eval/mimic-iv-3.1/e2a` (57 s, 20 GB; rerun 2026-10-30 on the CDM with MIMIC-IV-ED,
microbiology, ICU procedures and dates), mimic-code at `303d26c6`. Per-variable tables are in
`data/eval/mimic-iv-3.1/e2a/*.csv`.

mimic-code's SQL runs unchanged on both sides: on the source parquet, and on MIMIC-shaped tables
rebuilt from CDM fields only (`evaluation/src/mimic_eval/cdm_tables.py`); see
[the plan](evaluation.md#e2a-mimic-code-concepts-same-logic-on-both-sides) for how each table is
rebuilt. Labs and vital signs are selected on the CDM by standard concept and unit, as an analyst
would, so records of other items with the same concept and unit are included.

The CDM also holds MIMIC-IV-ED, which mimic-code does not read: an admission that began in the ED
has the ED stay's own diagnoses, and the ED charted vital signs of 58,711 values after the patient
reached the ICU. The comparisons are made like for like, leaving those records out by CDM fields
alone (ED diagnoses are the only ICD-coded records with a `visit_detail_id`; ED vital signs have
the ED column name, not an item id, as source value), and again with them (`cdm_with_ed`), as an
analyst would get them. Like for like, every result is identical to the run before MIMIC-IV-ED
was added.

### Charlson comorbidity index (546,028 admissions)

| Variant | Components agreeing (of 17) | Index identical | Main cause of differences |
|---|---:|---:|---|
| Source values, all event tables | 17 at 100% | 99.998% | 2 admissions change age band: their CDM visit starts at an ED arrival on the evening of 31 December (visits are widened to cover their transfers), so age at admission is one year less (221 admissions) |
| Source values, with ED diagnoses | 0 at 100% (all ≥ 99.5%) | 99.2% | the ED stay's diagnoses add to the admission's, e.g. diabetes without complications for 2,715 more admissions |
| Source values, `condition_occurrence` only | 12 at 100% | 94.5% | diagnoses routed to other tables by their concept's domain: metastasis codes (Cancer Modifier, in measurement; 20,609 admissions lose metastatic solid tumour), dialysis and transplant status, "history of" codes (observation; peripheral vascular 4,165, renal 2,953, mild liver 2,598) |
| Standard concepts, mapped | 0 at 100% | 68.0% | one-to-many mappings: a code mapped to several concepts puts each in the set, e.g. ICD-9 290.21 maps to Dementia and Depressive disorder, E11.62x to Type 2 diabetes and a complication, so depression counts as dementia (92.0% agreement) and type 2 diabetes as diabetes with complications (84.4%) |
| Standard concepts, specific | 0 at 100% | 73.0% | concepts also reached by codes outside the component are dropped: Type 2 diabetes leaves "without complications" (85.9%), CKD stages "renal disease" (94.1%) |
| Standard concepts with descendants | 0 at 100% | 21.9% | descendants of broad targets: rheumatic disease 53.6%, cerebrovascular 75.1%, dementia 75.0% |

The standard-concept variants read every record with the concepts, so they include the ED
diagnoses; before MIMIC-IV-ED their index agreement was 68.8%, 73.2% and 22.3%, and 3, 3 and 1
components agreed fully.

**Conservation of diagnoses holds:** every component agrees on every admission when the CDM is
queried by source value across all event tables. A query of `condition_occurrence` alone misses
the diagnosis codes that the CDM, following their concepts' domains, stores elsewhere; this is
by design of the CDM, but it is the most likely way for a CDM user to undercount comorbidities.

**Translation to standard concepts loses the definition's precision, not data.** No concept-set
construction reproduces mimic-code's index: ICD codes that combine two meanings are split into
two concepts, and neither concept alone means what the code means. The mapped and specific sets
bound the effect (an upper and a lower set). This concerns any ICD-defined phenotype applied to
an OMOP CDM, and should be read alongside E2b.

The same definitions also needed a 'Maps to value' aware concept set: "history of" codes map to
History of event with the condition as value, and a set of mapped concepts alone would count
every history code for every component.

### KDIGO creatinine (94,144 ICU stays)

| | Identical | Different | CDM only |
|---|---:|---:|---:|
| creatinine at each time (811,341 times) | 99.99% | 1 | 70 |
| lowest in past 48 h | 99.99% | 32 | 66 |
| lowest in past 7 days | 99.98% | 43 | 70 |
| maximum creatinine stage per stay | 99.999% | – | 1 stay |

The CDM-only values are creatinine results of items 52546 (Creatinine, blood, 1,273 rows) and
51081 ("Creatinine, Serum" with fluid Urine, 717 rows), which CVB maps to the same LOINC
concept and unit as mimic-code's 50912.

### First-day labs (86 variables) and vital signs (24 variables)

71 of 86 lab variables are identical for at least 99.9% of stays; no value is ever changed by
the ETL (every difference traced below is a different set of records).

| Variable | Identical | Cause |
|---|---:|---|
| hemoglobin min / max | 89.4% / 82.9% | blood gas hemoglobin (50811, 144,055 rows) has the same concept (718-7) and unit as 51222; mimic-code uses 51222 only |
| D-dimer min / max | 55.1% | two D-dimer items (50915, 51196) share the concept and both units; mimic-code uses 51196 only |
| thrombin time, immature granulocytes, albumin | 98.1–99.8% | ICU chart items with the same concept and unit (227469, 229356, 227456); the chart item for immature granulocytes holds 999999 placeholders |

Vital signs: 23 of 24 variables are identical for at least 99.0% of stays (heart rate, mean
pressure, respiratory rate, temperature and SpO2 for at least 99.9%); diastolic pressure
differs most (97.9% of means identical), because manual and other diastolic items (224643,
224311, ...) share its concept. For heart rate no row differs in value and the CDM has no row
the source lacks. With the ED's vital signs (`first_day_vitalsign_with_ed.csv`), the means of
systolic and diastolic pressure, respiratory rate and temperature differ in 4-7% of stays (sbp
94.2%, dbp 93.1%, respiratory rate 95.1%, temperature 96.1% identical): the ED's values share
those items' concept and unit; heart rate, mean pressure, SpO2 and glucose do not change. 14
stays have no first-day vital signs on the CDM: they are the 14 ICU stays without an
`outtime` in MIMIC's own `icustays`, so no care unit stay can be matched to them.

Rows are attributed to an ICU stay on the CDM through their `visit_detail_id`. MIMIC charts
0.6% of chart rows outside their ICU stay's in and out times while keeping its `stay_id`, and
the ETL first attached them to the care unit stay covering their time (often the ED or PACU);
first-day vital sign means then differed in 1.2–3.4% of stays. Rows up to 6 hours before or
1 hour after their ICU stay now keep that stay
([review R30](review.md#r30-chart-rows-outside-their-icu-stay)), and transfers without an
`outtime` end at the next transfer ([visit_detail](etl/visit_detail.md)); together these brought
the means to 97.9–99.9% identical (heart rate 98.23% → 99.94%) and reduced chart measurements
without a visit detail from 348,374 to 70,296.

**Found for CVB** ([review R26](review.md#r26-feedback-for-cvb)): fingerstick glucose (225664,
1.0 M rows, no unit in MIMIC, median 140, so mg/dL) is mapped to LOINC 14743-9, glucose in
moles per volume, and gets no unit; urine "Creatinine, Serum" (51081) is mapped to serum
creatinine.

## E2b. OHDSI cohorts on the CDM and on the source

`cd evaluation && Rscript R/generate_cohorts.R <cdm.duckdb> <out>/circe <ids>` (CohortGenerator,
PhenotypeLibrary v3.37.0), then `uv run mimic-eval e2b --source ../data/mimic-iv-3.1-parquet --cdm
../data/mimic-iv-3.1-cdm --vocab ../data/vocab --circe <out>/circe --cohorts <ids> --out <out>`
(about 30 s for eleven cohorts). Method in [the plan](evaluation.md#e2b-ohdsi-phenotypes-on-the-cdm-and-on-the-source).

**The evaluator reproduces Circe exactly:** for every cohort, the evaluator on the CDM and
CohortGenerator on the DuckDB export give the same entries (0 differing either way), so the
source-side runs use the same logic as the official engine.

Eleven cohorts, CDM against source (source as reference). "CDM periods" runs the source with
the CDM's observation periods, which separates their effect. Entries: CDM entries with a source
entry starting the same day, and within one day.

| Cohort | Persons source / CDM | Jaccard | Jaccard, CDM periods | Entries same day / within 1 day | Remaining cause |
|---|---:|---:|---:|---:|---|
| 362 Acute kidney injury | 42,143 / 42,143 | 1.000 | 1.000 | 99.87% / 100.00% | visit boundaries |
| 411 Sepsis or septic shock | 18,571 / 18,570 | 1.000 | 1.000 | 99.90% / 100% | observation periods (1 person) |
| 405 Atrial fibrillation or flutter | 35,889 / 35,887 | 1.000 | 1.000 | 99.90% / 100.00% | observation periods (2 persons) |
| 1005 Venous thromboembolism | 12,673 / 12,673 | 1.000 | 1.000 | 99.82% / 100% | |
| 359 Acute respiratory failure | 21,165 / 21,165 | 1.000 | 1.000 | 99.91% / 100% | |
| 95 Delirium | 10,330 / 10,330 | 1.000 | 1.000 | 99.77% / 100% | |
| 957 Type 2 diabetes mellitus | 28,257 / 26,228 | 0.913 | 0.990 | 98.99% / 99.17% | observation periods; 257 more persons on the CDM: ED drug records the source side does not map |
| 71 Acute myocardial infarction with inpatient admission | 13,530 / 13,384 | 0.989 | 0.989 | 99.87% / 99.94% | 146 persons in EU or ambulatory observation stays (0.771 before R3 was revised) |
| 938 Hospitalization with heart failure | 32,965 / 31,950 | 0.969 | 0.969 | 99.33% / 99.50% | 1,015 persons in observation stays |
| 75 Ischemic stroke with inpatient admission | 9,567 / 9,135 | 0.955 | 0.955 | 99.89% / 100% | 432 persons in observation stays |
| 77 GI bleeding with inpatient admission | 12,927 / 11,959 | 0.925 | 0.925 | 99.92% / 100% | 968 persons in observation stays |

No cohort has a person on the CDM that the source lacks, except type 2 diabetes (below). The
entries of AKI that start a day apart were traced to visit boundaries (below); the other cohorts'
same-day shares (99.3-99.9%) are of the same size and not traced one by one.

Causes, each traced to the records involved:
- **Observation stays (cohorts requiring an inpatient or ER visit).** Every person missing from
  cohorts 71, 75, 77 and 938 on the CDM has the diagnosis in an EU or ambulatory observation stay,
  typed Observation Room (581385), an outpatient visit, by decision (R3).
- **Observation admissions (AMI, first run).** In the first run, all 3,093 AMI persons missing on the CDM had
  their AMI diagnosis in the CDM, inside an observation period, but in an admission the ETL typed
  Observation Room (581385), which sits under Outpatient Visit and is not in the cohort's
  "Inpatient or ER visit" set. `OBSERVATION ADMIT` admissions (84,437) last a median 4.6 days, as
  long as urgent (4.0) and emergency (3.8) admissions; `EU OBSERVATION` (119,456) a median 0.7
  days. R3 was revised accordingly ([review R3](review.md#r3-observation-admissions-with-icu-stays)):
  `OBSERVATION ADMIT` and `DIRECT OBSERVATION` are now inpatient stays, and the 146 persons still
  missing are those in EU (135) and ambulatory (11) observation stays, outpatient by design.
- **Observation periods (T2DM).** Every T2DM person found on the source only fails the cohort's
  additional criteria on the CDM because the second diagnosis, drug or HbA1c lies outside the
  index's observation period (548 days without care start a new one); with the CDM's periods on
  the source side, sensitivity is 1.000. The 257 persons found on the CDM only meet "two drug
  exposures" there with MIMIC-IV-ED records: 237 with Pyxis dispensing (mostly with medication
  lists), whose concepts come from the GSN crosswalk the ETL derives
  ([R32](review.md#r32-a-gsn-crosswalk-derived-from-mimics-prescriptions)) and the source side
  leaves out, and about 6 with prescriptions the ETL maps by CVB's NDC and formulary codes.
- **Visit boundaries (AKI).** Diagnoses are dated at their visit's start. 54 entries are a day or
  two earlier on the CDM, whose visit is widened to an earlier transfer, and 31 a day later,
  whose ED part starts at the ED transfer rather than `edregtime`
  ([R13](review.md#r13-visit-boundaries)).

Two evaluator choices made the comparison like for like, found on the way:
- source rows are routed by their concept's domain, as the CDM is: AKI's transplant exclusion
  has a procedure criteria whose concept set includes concepts that diagnosis codes map to, which
  a procedure criteria on the CDM cannot see;
- an eMAR administration takes the concept of its order's NDC (`pharmacy_id`), as the ETL does;
  by medication name, the source missed administrations that make up T2DM's "two drug
  exposures". On both sides an order and its administration are two exposures, so one
  metformin order with one administration meets that criteria
  ([R17](review.md#r17-orders-and-administrations-are-both-recorded)).

## P4. The CDM in MEDS

`cd evaluation && uv run mimic-eval omop-meds --cdm ../data/mimic-iv-3.1-cdm --source-meds
../data/meds/mimic-iv-3.1-meds-0.1.0 --out ../data/meds/cdm-meds-main` (110 s, 69 GB;
`mimic_eval/omop_meds.py`, choices in its docstring and [the plan](evaluation.md#inputs)).

- 718,049,916 events for the same 364,627 subjects, in the same split and shard files as the
  source MEDS (877,766,303 events; the two are not expected to match, since they split records
  into events differently and cover different source tables). Every record of the six clinical event tables is one event (checked
  against the CDM's row counts), plus 12,972,300 birth, demographic, death, visit, care unit and
  ICU stay events.
- ICU stays: 97,742 against MIMIC's 94,458 `icustays`; 88,765 start at the same minute and 95%
  within 53 minutes, but 4,625 are more than an hour from any source ICU admission. Merging
  consecutive ICU care unit stays does not reproduce MIMIC's stays exactly (stays interrupted by
  a short transfer, as to the OR, are the likely cause). E3a does not depend on this, since its
  labels and prediction times come from the source; E3b, which defines the ICU tasks from the CDM,
  does, and is where it will be resolved.

## E3a. Same labels, different features

`cd evaluation/meds && E3A_CPUS=32 uv run python e3a.py <MEDS dataset> <labels> <work>`, then
`uv run python compare.py <source predictions> <cdm predictions>`. The labels are extracted once
from the source MEDS by `labels.py` (ACES 0.7.3 with MEDS-DEV's MIMIC-IV predicates). Models
(`e3a.py`):
- MEDS-Tab XGBoost, 20 Optuna trials;
- Cyclops L1 logistic regression, with its prior variance chosen by cross-validation (Brent
  search, 10 folds) on the same feature matrices.

Both are scored on the held-out split.

The first run found two faults, one in the ETL and one in MEDS-Tab. Both are fixed and the run is
being repeated.

### First run: what it found

The CDM was never worse than the source, and often better: 7 of 16 task–model pairs were higher
on the CDM with intervals excluding 0, most clearly metabolic acidosis (+0.021 and +0.027).
Equivalence held for 3 of 16. Tracing the difference found the faults.

**A time leak in the ETL (ICU dates).**
- XGBoost's two largest features for metabolic acidosis on the CDM were peripheral IV and
  arterial line insertions in the last 7 days, 17% of the total gain. Nearly all come from
  `icu.datetimeevents`, which the source MEDS does not use.
- A date-only item (`valueuom` 'Date', about half of these records) was dated at midnight, so a
  line inserted that afternoon appeared hours before it happened. A model predicting during the
  day saw it.
- Labels with such a record visible before it was charted:
  - metabolic acidosis: 10% of labels, positive rate 16.2% against 5.1% without;
  - hypotension: 51%, 46.3% against 25.0%;
  - thrombocytopenia: 10%, 14.3% against 4.5%;
  - ICU mortality: 46%, 11.8% against 7.1%.
- Fix (ETL): these records keep their date and no datetime. The converter already places a
  date-only record at the end of its day.
- Left as it is: about 2,000 labels per task see a record with a real time that was charted
  later, such as a line placed before ICU arrival and charted on arrival. That is the event's
  time in OMOP's sense, and the source treats ICU procedures the same way.

**Empty value features (MEDS-Tab 0.2.0).**
- Every `value/*` matrix was empty: `get_long_value_df` filtered raw codes against
  `/value`-suffixed feature names
  ([MEDS-Tab #168](https://github.com/mmcdermott/MEDS_Tabular_AutoML/issues/168), fixed on main
  and not released). Both models, on both sides, saw code counts only.
- Found because the L1 LR's held-out AUROC for thrombocytopenia was unchanged without the value
  features (0.7694 and 0.7693). A wider grid search (variance 1e-4 to 10) also gave 0.7690, so
  the CV search was not the cause of XGBoost's lead.
- Fix: MEDS-Tab pinned to main at `586de945`, and `e3a.py` fails if the value matrices are
  empty.

First run, counts only, with the leak (kept for the record; outputs in `e3a-counts-only`).
AUROC difference CDM minus source with its 90% interval:

| Task | Held-out samples (positive) | Model | AUROC source | AUROC CDM | Difference [90% CI] | Equivalent | AUPRC source / CDM | Risk correlation | Top-decile overlap |
|---|---|---|---|---|---|---|---|---|---|
| ICU mortality | 4,584 (423) | XGBoost | 0.844 | 0.844 | +0.000 [−0.005, +0.005] | yes | 0.369 / 0.373 | 0.95 | 0.80 |
| | | LR | 0.831 | 0.834 | +0.003 [−0.004, +0.011] | no | 0.323 / 0.349 | 0.91 | 0.75 |
| Anemia | 1,049 (670) | XGBoost | 0.823 | 0.831 | +0.008 [−0.000, +0.017] | no | 0.890 / 0.897 | 0.95 | 0.55 |
| | | LR | 0.812 | 0.820 | +0.008 [−0.003, +0.019] | no | 0.875 / 0.885 | 0.92 | 0.56 |
| Hypotension | 2,110 (699) | XGBoost | 0.757 | 0.771 | +0.014 [+0.004, +0.024] | no | 0.631 / 0.651 | 0.92 | 0.73 |
| | | LR | 0.759 | 0.767 | +0.008 [+0.002, +0.015] | no | 0.631 / 0.645 | 0.95 | 0.74 |
| Leukocytosis | 2,253 (577) | XGBoost | 0.781 | 0.784 | +0.003 [−0.004, +0.010] | yes | 0.602 / 0.599 | 0.96 | 0.85 |
| | | LR | 0.771 | 0.780 | +0.009 [+0.001, +0.017] | no | 0.599 / 0.605 | 0.94 | 0.78 |
| Metabolic acidosis | 10,888 (679) | XGBoost | 0.717 | 0.738 | +0.021 [+0.010, +0.031] | no | 0.185 / 0.198 | 0.84 | 0.66 |
| | | LR | 0.703 | 0.729 | +0.027 [+0.016, +0.037] | no | 0.168 / 0.184 | 0.84 | 0.66 |
| Hyponatremia | 13,315 (678) | XGBoost | 0.731 | 0.732 | +0.001 [−0.007, +0.009] | yes | 0.160 / 0.163 | 0.90 | 0.72 |
| | | LR | 0.718 | 0.719 | +0.001 [−0.008, +0.010] | no | 0.146 / 0.147 | 0.88 | 0.70 |
| Thrombocytopenia | 14,790 (765) | XGBoost | 0.784 | 0.791 | +0.007 [+0.001, +0.013] | no | 0.229 / 0.247 | 0.91 | 0.79 |
| | | LR | 0.769 | 0.780 | +0.010 [+0.003, +0.017] | no | 0.203 / 0.225 | 0.89 | 0.73 |
| Elevated creatinine | 15,179 (273) | XGBoost | 0.757 | 0.772 | +0.015 [−0.001, +0.031] | no | 0.076 / 0.085 | 0.76 | 0.59 |
| | | LR | 0.766 | 0.767 | +0.001 [−0.014, +0.016] | no | 0.073 / 0.091 | 0.76 | 0.63 |

XGBoost minus LR in that run (same side and samples): +0.004 to +0.015 AUROC, clear in 5 of 8
tasks on the source and 5 of 10 on the CDM, never clearly negative. XGBoost's calibration index
was equal or lower.

### Second run

All ten tasks on both sides, with value features (MEDS-Tab `586de945`). The CDM side is built from
the corrected CDM (`cdm-meds-dateonly`). Predictions were scored with `compare.py` (2,000
bootstrap replicates over subjects).

| Task | Held-out samples (positive) | Model | AUROC source | AUROC CDM | Difference [90% CI] | Equivalent | AUPRC source / CDM | Risk correlation | Top-decile overlap |
|---|---|---|---|---|---|---|---|---|---|
| ICU mortality | 4,584 (423) | XGBoost | 0.856 | 0.864 | +0.008 [+0.002, +0.014] | no | 0.381 / 0.402 | 0.93 | 0.80 |
|  |  | LR | 0.840 | 0.846 | +0.006 [−0.001, +0.014] | no | 0.339 / 0.362 | 0.91 | 0.77 |
| In-hospital mortality | 33,139 (845) | XGBoost | 0.900 | 0.910 | +0.010 [+0.006, +0.014] | no | 0.318 / 0.349 | 0.92 | 0.81 |
|  |  | LR | 0.887 | 0.895 | +0.008 [+0.003, +0.014] | no | 0.284 / 0.286 | 0.91 | 0.77 |
| Readmission, 30 days | 42,531 (9,960) | XGBoost | 0.755 | 0.744 | −0.011 [−0.013, −0.009] | no | 0.542 / 0.513 | 0.90 | 0.77 |
|  |  | LR | 0.742 | 0.728 | −0.013 [−0.016, −0.010] | no | 0.504 / 0.485 | 0.90 | 0.75 |
| Anemia | 1,049 (670) | XGBoost | 0.884 | 0.884 | +0.000 [−0.006, +0.006] | yes | 0.937 / 0.935 | 0.97 | 0.90 |
|  |  | LR | 0.860 | 0.863 | +0.002 [−0.009, +0.014] | no | 0.921 / 0.922 | 0.89 | 0.73 |
| Hypotension | 2,110 (699) | XGBoost | 0.774 | 0.774 | −0.000 [−0.006, +0.005] | yes | 0.651 / 0.649 | 0.96 | 0.82 |
|  |  | LR | 0.763 | 0.767 | +0.004 [−0.002, +0.010] | no | 0.633 / 0.643 | 0.96 | 0.80 |
| Leukocytosis | 2,253 (577) | XGBoost | 0.828 | 0.846 | +0.018 [+0.010, +0.025] | no | 0.649 / 0.692 | 0.93 | 0.77 |
|  |  | LR | 0.801 | 0.823 | +0.021 [+0.014, +0.028] | no | 0.625 / 0.666 | 0.93 | 0.75 |
| Metabolic acidosis | 10,888 (679) | XGBoost | 0.807 | 0.821 | +0.014 [+0.007, +0.020] | no | 0.270 / 0.286 | 0.90 | 0.73 |
|  |  | LR | 0.794 | 0.812 | +0.018 [+0.010, +0.026] | no | 0.240 / 0.253 | 0.85 | 0.68 |
| Hyponatremia | 13,315 (678) | XGBoost | 0.823 | 0.830 | +0.008 [+0.002, +0.013] | no | 0.215 / 0.250 | 0.88 | 0.75 |
|  |  | LR | 0.717 | 0.757 | +0.040 [+0.029, +0.051] | no | 0.145 / 0.163 | 0.77 | 0.61 |
| Thrombocytopenia | 14,790 (765) | XGBoost | 0.921 | 0.926 | +0.005 [+0.002, +0.008] | yes | 0.437 / 0.464 | 0.93 | 0.85 |
|  |  | LR | 0.897 | 0.906 | +0.009 [+0.004, +0.013] | no | 0.354 / 0.376 | 0.93 | 0.77 |
| Elevated creatinine | 15,179 (273) | XGBoost | 0.890 | 0.899 | +0.009 [+0.003, +0.016] | no | 0.220 / 0.257 | 0.86 | 0.77 |
|  |  | LR | 0.872 | 0.893 | +0.021 [+0.013, +0.029] | no | 0.177 / 0.242 | 0.78 | 0.71 |

XGBoost minus LR (AUROC on the same side and samples, with the 95% interval):

| Task | Source | CDM |
|---|---|---|
| ICU mortality | +0.016 [+0.005, +0.028] | +0.017 [+0.006, +0.030] |
| In-hospital mortality | +0.013 [+0.007, +0.020] | +0.015 [+0.009, +0.021] |
| Readmission, 30 days | +0.013 [+0.010, +0.017] | +0.016 [+0.013, +0.019] |
| Anemia | +0.024 [+0.010, +0.040] | +0.022 [+0.005, +0.039] |
| Hypotension | +0.012 [+0.002, +0.021] | +0.007 [−0.003, +0.016] |
| Leukocytosis | +0.027 [+0.014, +0.040] | +0.024 [+0.012, +0.035] |
| Metabolic acidosis | +0.013 [+0.003, +0.024] | +0.009 [−0.000, +0.018] |
| Hyponatremia | +0.106 [+0.088, +0.123] | +0.074 [+0.058, +0.089] |
| Thrombocytopenia | +0.024 [+0.017, +0.031] | +0.020 [+0.013, +0.027] |
| Elevated creatinine | +0.017 [+0.002, +0.032] | +0.006 [−0.008, +0.020] |

**Pass criterion not met.** Equivalence holds for 3 of 10 tasks with XGBoost (anemia, hypotension,
thrombocytopenia) and none with the LR.

**The CDM is mostly higher.**
- With XGBoost, the CDM is higher with an interval excluding 0 in 6 tasks, by +0.005 to +0.018.
  The largest are leukocytosis (+0.018), metabolic acidosis (+0.014) and in-hospital mortality
  (+0.010).
- Readmission is the exception: the CDM is lower, −0.011 [−0.013, −0.009] with XGBoost and
  −0.013 with the LR.
- Calibration is close on both sides: the ICI differs by at most 0.006, and the slopes are
  0.9–1.2 on both.
- Still to find: why the CDM is higher (extra information, or a remaining leak; the input-matched
  variant and feature importances will separate these), and what the CDM lacks for readmission.
  Discharge disposition, admission type and insurance are candidates: the converter does not
  emit them as events, and the source MEDS has them.

**Value features matter.** XGBoost's AUROC rose from the first run's 0.72–0.84 to 0.77–0.93. On
the source:
- thrombocytopenia 0.784 → 0.921;
- elevated creatinine 0.757 → 0.890;
- hyponatremia 0.731 → 0.823.

**XGBoost against the LR.**
- XGBoost is ahead in every task on both sides, by 0.006–0.027, and is clearly ahead in 17 of 20
  task–side pairs.
- Hyponatremia is the outlier: +0.106 on the source and +0.074 on the CDM.
- Calibration (ICI) is equal or slightly better with XGBoost.
- The LR's value features are scaled by their maximum absolute value, which puts them at a small
  fraction of the counts' range, so the L1 penalty suppresses them. An LR with quintile
  indicators for the values is the next test.

### Third run: LightGBM and Cyclops (`e3.py`)

`cd evaluation/meds && python e3.py all --source <MEDS> --cdm <MEDS>` (on the server, in the
Cyclops venv, every fit under the booking rule). What changed from the second run:
- **Features:** `featurize.py` computes MEDS-Tab's features itself: windows of 1, 7 and 30 days
  and the whole history; per code its count and its values' sum, minimum and maximum; codes seen
  at least 10 times in the training split. MEDS-Tab 0.2 drops the last event of each time, counts
  times rather than events and takes min/max with implicit zeros (`featurize.py`'s docstring).
  Source 167,614 columns, CDM 118,804.
- **Models:**
  - LightGBM, with hyperparameters tuned once per task on the source (20 Optuna trials) and
    frozen;
  - the Cyclops L1 LR (build `ebb89849`), its prior variance frozen at what 3-fold
    cross-validation from 0.01 (PLP's start) chose;
  - the same LR with quintile indicators for the values.

  The columns FeatureExtraction drops as redundant are removed first. XGBoost is kept for final
  runs.
- **Decomposition** (`--decompose`): the LR on code indicators only and on counts only, and
  LightGBM with stumps.
- **Pairing:** `compare` pairs only the fits the run made (`e3.planned_fits`). Before, it paired every fit
  on disk, giving two rows for some task–model pairs.

| Task | Held-out samples (positive) | Model | AUROC source | AUROC CDM | Difference [90% CI] | Equivalent | AUPRC source / CDM | Risk correlation |
|---|---|---|---|---|---|---|---|---|
| ICU mortality | 4,584 (423) | LightGBM | 0.878 | 0.881 | +0.003 [−0.002, +0.008] | yes | 0.444 / 0.453 | 0.94 |
|  |  | LR | 0.864 | 0.862 | −0.002 [−0.006, +0.003] | yes | 0.391 / 0.397 | 0.96 |
|  |  | LR, quintiles | 0.858 | 0.863 | +0.005 [+0.002, +0.008] | yes | 0.400 / 0.409 | 0.98 |
| In-hospital mortality | 33,139 (845) | LightGBM | 0.923 | 0.925 | +0.002 [−0.001, +0.004] | yes | 0.382 / 0.384 | 0.95 |
|  |  | LR | 0.906 | 0.906 | +0.000 [−0.003, +0.004] | yes | 0.331 / 0.324 | 0.95 |
|  |  | LR, quintiles | 0.902 | 0.906 | +0.004 [+0.002, +0.006] | yes | 0.314 / 0.314 | 0.96 |
| Readmission, 30 days | 42,531 (9,960) | LightGBM | 0.765 | 0.750 | −0.015 [−0.017, −0.013] | no | 0.561 / 0.525 | 0.92 |
|  |  | LR | 0.751 | 0.736 | −0.016 [−0.018, −0.013] | no | 0.522 / 0.493 | 0.91 |
|  |  | LR, quintiles | 0.743 | 0.730 | −0.014 [−0.016, −0.011] | no | 0.508 / 0.485 | 0.92 |
| Anemia | 1,049 (670) | LightGBM | 0.898 | 0.902 | +0.004 [−0.001, +0.010] | yes | 0.944 / 0.945 | 0.97 |
|  |  | LR | 0.885 | 0.882 | −0.003 [−0.007, +0.002] | yes | 0.935 / 0.932 | 0.98 |
|  |  | LR, quintiles | 0.891 | 0.889 | −0.002 [−0.005, +0.002] | yes | 0.939 / 0.938 | 0.99 |
| Hypotension | 2,110 (699) | LightGBM | 0.783 | 0.785 | +0.002 [−0.004, +0.007] | yes | 0.660 / 0.669 | 0.96 |
|  |  | LR | 0.770 | 0.767 | −0.003 [−0.008, +0.002] | yes | 0.638 / 0.636 | 0.97 |
|  |  | LR, quintiles | 0.772 | 0.776 | +0.004 [+0.000, +0.008] | yes | 0.638 / 0.646 | 0.98 |
| Leukocytosis | 2,253 (577) | LightGBM | 0.858 | 0.859 | +0.001 [−0.004, +0.006] | yes | 0.707 / 0.711 | 0.97 |
|  |  | LR | 0.840 | 0.843 | +0.003 [−0.002, +0.007] | yes | 0.683 / 0.688 | 0.98 |
|  |  | LR, quintiles | 0.841 | 0.837 | −0.004 [−0.008, +0.001] | yes | 0.682 / 0.674 | 0.98 |
| Metabolic acidosis | 10,888 (679) | LightGBM | 0.824 | 0.837 | +0.013 [+0.007, +0.019] | no | 0.292 / 0.345 | 0.85 |
|  |  | LR | 0.796 | 0.816 | +0.020 [+0.013, +0.027] | no | 0.252 / 0.254 | 0.88 |
|  |  | LR, quintiles | 0.808 | 0.821 | +0.013 [+0.008, +0.019] | no | 0.250 / 0.311 | 0.84 |
| Hyponatremia | 13,315 (678) | LightGBM | 0.834 | 0.842 | +0.008 [+0.003, +0.013] | no | 0.236 / 0.289 | 0.79 |
|  |  | LR | 0.751 | 0.754 | +0.003 [−0.005, +0.010] | yes | 0.161 / 0.168 | 0.90 |
|  |  | LR, quintiles | 0.823 | 0.825 | +0.002 [−0.002, +0.007] | yes | 0.204 / 0.214 | 0.93 |
| Thrombocytopenia | 14,790 (765) | LightGBM | 0.933 | 0.936 | +0.003 [+0.000, +0.005] | yes | 0.503 / 0.519 | 0.93 |
|  |  | LR | 0.921 | 0.925 | +0.004 [+0.002, +0.007] | yes | 0.451 / 0.459 | 0.95 |
|  |  | LR, quintiles | 0.919 | 0.921 | +0.003 [+0.001, +0.005] | yes | 0.425 / 0.421 | 0.96 |
| Elevated creatinine | 15,179 (273) | LightGBM | 0.911 | 0.916 | +0.004 [−0.001, +0.010] | no | 0.257 / 0.332 | 0.77 |
|  |  | LR | 0.893 | 0.903 | +0.011 [+0.005, +0.016] | no | 0.206 / 0.262 | 0.86 |
|  |  | LR, quintiles | 0.889 | 0.897 | +0.008 [+0.003, +0.015] | no | 0.196 / 0.207 | 0.93 |

**Equivalence holds for 6 of 10 tasks with LightGBM and 7 of 10 with each LR.**
- Not equivalent with any model: readmission (the CDM lower), metabolic acidosis and elevated
  creatinine (the CDM higher).
- Hyponatremia is not equivalent with LightGBM only (+0.008).
- Every other difference lies within ±0.005.

Both remaining patterns have a cause outside the ETL's content, found below.

**Readmission: the CDM's discharge comes later.**
- The task predicts at the source's discharge time (`HOSPITAL_DISCHARGE`). The CDM's visit ends
  at the later of the discharge and the last transfer's `outtime`
  ([visit_occurrence](etl/visit_occurrence.md)). That is later for 98.5% of the labels, by a
  median of 224 s.
- The converter places the admission's billing diagnoses at its visit's end (P4). On the CDM side
  they therefore fall after the prediction time, and the model does not see the admission it is
  predicting from. The source dates diagnoses and DRGs at the discharge itself, and the window
  includes its end.
- Test (readmission only, refit): the CDM side predicting at its own visit end, paired with the
  source at the source's time. LightGBM −0.009 [−0.011, −0.007], the LR −0.008 [−0.009, −0.006]
  (equivalent). This explains half the gap.
- Candidates for the rest, not yet tested: discharge location, admission type and insurance, which
  the source MEDS has as events and the converter does not emit.
- Neither is an ETL fault: the CDM's times are right, and the comparison asks the CDM a question
  timed by the source.

**Lab tasks: OMOP has no result time.**
- A MIMIC lab has the time the specimen was drawn (`charttime`) and the time the result was stored
  (`storetime`, a median of 82 minutes later). OMOP's measurement has one time, and the ETL uses
  the specimen time. The source MEDS dates results at `storetime`.
- So at the prediction time the CDM side sees results the source side does not yet have. For 4–7%
  of the positives that includes the abnormal result the label is about.
- Split of the held-out labels (refit not needed): those with a hosp lab drawn at or before the
  prediction time and stored after it ("pending"), and the rest. Difference CDM minus source,
  AUROC [90% CI]:

| Task | Pending labels (positive) of all | LightGBM, pending | LightGBM, rest | LR, pending | LR, rest |
|---|---|---|---|---|---|
| Elevated creatinine | 3,792 (86) of 15,179 | +0.018 [+0.008, +0.030] | −0.002 [−0.008, +0.004] | +0.027 [+0.016, +0.040] | +0.003 [−0.003, +0.009] |
| Hyponatremia | 3,405 (182) of 13,315 | +0.038 [+0.026, +0.051] | −0.003 [−0.007, +0.002] | +0.017 [−0.001, +0.034] | −0.003 [−0.010, +0.006] |
| Metabolic acidosis | 2,844 (189) of 10,888 | +0.055 [+0.039, +0.072] | −0.004 [−0.009, +0.001] | +0.051 [+0.036, +0.067] | +0.008 [−0.000, +0.016] |
| Thrombocytopenia | 3,619 (177) of 14,790 | +0.018 [+0.010, +0.026] | −0.002 [−0.004, +0.000] | +0.012 [+0.005, +0.021] | +0.002 [−0.000, +0.004] |
| Anemia | 295 (178) of 1,049 | +0.012 [+0.001, +0.023] | +0.002 [−0.004, +0.009] | +0.001 [−0.007, +0.011] | −0.004 [−0.010, +0.002] |
| Leukocytosis | 689 (155) of 2,253 | −0.002 [−0.011, +0.007] | +0.002 [−0.003, +0.008] | +0.005 [−0.002, +0.013] | +0.002 [−0.004, +0.006] |
| Hypotension | 630 (226) of 2,110 | +0.004 [−0.006, +0.014] | +0.001 [−0.006, +0.008] | −0.003 [−0.011, +0.006] | −0.003 [−0.010, +0.003] |
| In-hospital mortality | 7,833 (306) of 33,139 | +0.003 [−0.002, +0.008] | +0.001 [−0.002, +0.003] | +0.000 [−0.005, +0.006] | +0.001 [−0.003, +0.004] |
| ICU mortality | 1,487 (183) of 4,584 | +0.001 [−0.008, +0.010] | +0.004 [−0.002, +0.011] | −0.004 [−0.012, +0.004] | +0.000 [−0.006, +0.006] |

- The CDM's lead on the lab tasks is all in the pending labels: +0.012 to +0.055 for the four
  chemistry and platelet tasks. Among the rest every difference lies within ±0.004 with LightGBM
  and between −0.004 and +0.008 with the LR.
- The LR on code indicators only shows no pending gap: it cannot read a value, only that a lab
  was drawn, and both sides see that. It does show a smaller CDM lead among the rest for metabolic
  acidosis (+0.017 [+0.006, +0.027]) and elevated creatinine (+0.014 [+0.001, +0.027]): which
  codes are present differs, a case for the input-matched variant.
- This is a property of the format, not a fault of the ETL: any OMOP model predicting within an
  admission sees lab results from when the specimen was drawn. It also means E3's lab tasks, as
  they stand, compare a leaky CDM side with a clean source side.

**Feature decomposition (source side, AUROC).**
- Counts add nothing over indicators: the LR on counts and on indicators differ by at most 0.011
  and in both directions.
- Values carry the lab tasks: the LR with values gains +0.12 (elevated creatinine), +0.13
  (thrombocytopenia), +0.08 to +0.09 (metabolic acidosis, hyponatremia with quintiles) and +0.06
  (anemia, leukocytosis) over indicators; +0.02 for mortality, under +0.01 for readmission and
  hypotension.
- LightGBM with stumps matches the better LR (−0.003 to +0.005), and the tuned trees add +0.006 to
  +0.019 over the stumps: LightGBM's lead over the LR is interactions.

### Featurization ladder (source side)

`e3.py run source <MEDS> --ladder`, then `e3.py ladder-report source <MEDS>`: refits on columns
cut from the same matrices, adding one thing per step, with LightGBM at its frozen hyperparameters
and the LR at the frozen variance of the nearest existing LR. Steps (`models.ladder_inputs`):
- `fe0`: each code present or not in the last 30 days and in all history (sex is a code). This
  has FeatureExtraction's shape (binary covariates in a short and a long window) but not its
  default set: the codes are the features' own, with no concept roll-up, the long window is all
  history rather than 365 days, and the defaults' measurement range groups, value-as-concept
  covariates, drugs overlapping the index, race, ethnicity, index year and month and four
  comorbidity indices are missing;
- `fe`: plus 5-year age groups (from the birth event; `featurize.py` makes no age);
- `fe_short`: plus the 1-day and 7-day windows;
- `fe_values`: plus each value as quintile indicators (a stand-in for FeatureExtraction's
  measurement range groups);
- `full_age`: the full features (counts and raw values) plus age.

Held-out AUROC; readmission with LightGBM only (its LRs run on coordinate descent for hours):

| Task | Model | fe0 | fe | fe_short | fe_values | full_age | full (main) |
|---|---|---|---|---|---|---|---|
| In-hospital mortality | LightGBM | 0.899 | 0.905 | 0.907 | 0.921 | 0.924 | 0.923 |
|  | LR | 0.886 | 0.895 | 0.898 | 0.913 | 0.910 | 0.906 |
| Readmission, 30 days | LightGBM | 0.757 | 0.758 | 0.760 | 0.762 | 0.765 | 0.765 |
| Elevated creatinine | LightGBM | 0.777 | 0.789 | 0.793 | 0.901 | 0.907 | 0.911 |
|  | LR | 0.763 | 0.766 | 0.775 | 0.889 | 0.892 | 0.893 |
| Anemia | LightGBM | 0.834 | 0.834 | 0.836 | 0.897 | 0.901 | 0.898 |
|  | LR | 0.829 | 0.831 | 0.830 | 0.895 | 0.886 | 0.885 |

Paired differences (95% CI over subjects):
- **All the extra featurization** (`fe` to `full_age`): +0.118 [+0.093, +0.143] (LightGBM) and
  +0.126 (LR) for elevated creatinine, +0.067 and +0.055 for anemia, +0.019 and +0.015 for
  in-hospital mortality, +0.007 [+0.005, +0.010] for readmission.
- **The values are nearly all of it:** quintile indicators add +0.108 and +0.114 (creatinine),
  +0.062 and +0.065 (anemia), +0.014 and +0.015 (mortality), +0.002 (readmission).
- **The short windows** add −0.001 to +0.009. **Counts and raw values** beyond the quintiles add
  between −0.009 and +0.006, clear only for readmission (+0.003).
- **Age** matters only without the values: +0.006 and +0.008 for mortality, +0.012 with LightGBM
  for creatinine. On the full features it adds at most +0.004 (LR, mortality), and −0.005
  [−0.009, −0.001] with LightGBM for creatinine (one fit at frozen hyperparameters). Adding age
  to `featurize.py` would change little, and both sides would get it alike.
- For E3c: on the lab tasks, PLP's result will depend mostly on whether its covariates carry the
  lab values (FeatureExtraction's measurement range groups), not on the data.

### FeatureExtraction's default covariates (CDM side)

`evaluation/meds/fe_defaults.py` with `evaluation/R/fe_covariates.R`. The E3 labels go into a
cohort table, one row per label. FeatureExtraction 3.14.0's `createDefaultCovariateSettings()`
builds the covariates from the CDM. They become E3 matrices: binary covariates as presence and the
comorbidity indices as values, kept if they occur in at least 0.1% of training labels. The same
LightGBM hyperparameters and binary-LR variances as E3 are used, and the labels and splits are
E3's. The tasks are in-hospital and ICU mortality, readmission and two lab tasks. Variants:
- `fe_default`: the settings as they are on the CDM as exported. The windows end on the
  prediction date, and billing diagnoses (type 32821) are dated at admission (`DATED`).
- `fe_clean`: the 24-hour tasks' windows end the day before the prediction date (`endDays` −1),
  and billing diagnoses sit at their visit's end. `condition_era` is rebuilt from them, and the
  observation, measurement and procedure billing rows are moved too. Readmission's window ends
  on the discharge date.
- `fe`: `fe_clean` without `DrugGroupEraOverlapping` (analysis 413; see below).
- `day`: E3's own features (`featurize.py` on the CDM in MEDS), predicting at the end of the last
  day FeatureExtraction sees. This is the same information as `fe_clean`, featurized by E3.

Readmission is scored without the 600 held-out positives whose next admission starts later on the
discharge date, since every window that ends on that date sees the readmission. This applies to
every column. Held-out AUROC, LightGBM / LR:

| Task | E3 source | E3 CDM | `day` | `fe` | `fe_clean` | `fe_default` |
|---|---|---|---|---|---|---|
| In-hospital mortality | 0.923 / 0.906 | 0.925 / 0.906 | 0.911 / 0.892 | 0.907 / 0.898 | 0.910 / 0.902 | 0.983 / 0.980 |
| ICU mortality | 0.878 / 0.864 | 0.881 / 0.862 | 0.854 / 0.832 | 0.838 / 0.819 | 0.844 / 0.828 | 0.941 / 0.929 |
| Readmission, 30 days | 0.756 / 0.741 | 0.746 / 0.734 | 0.757 / 0.744 | 0.744 / 0.734 | 0.836 / 0.818 | |
| Elevated creatinine | 0.911 / 0.893 | 0.916 / 0.903 | 0.854 / 0.844 | 0.821 / 0.813 | 0.824 / 0.814 | 0.949 / 0.939 |
| Thrombocytopenia | 0.933 / 0.921 | 0.936 / 0.925 | 0.907 / 0.896 | 0.803 / 0.795 | 0.802 / 0.796 | 0.916 / 0.911 |

Paired differences (95% CI over subjects):
- **`DrugGroupEraOverlapping` leaks the readmission.** It adds +0.092 [+0.085, +0.098] (LightGBM)
  and +0.084 (LR) on readmission, and its covariates top the univariate ranking. A drug era joins
  exposures up to 30 days apart and ends with the last one. An era from the index stay that
  ended before the discharge date therefore stretches past it when the readmission gives the same
  ingredient again, and "era overlapping the index date" turns true because of the outcome. The
  analysis is computed at the index date whatever `endDays` is. On the 24-hour tasks it adds
  −0.002 to +0.009.
- **Day-level windows as defaulted leak the 24-hour tasks.** `fe_clean` to `fe_default`: +0.073
  to +0.125. Examples: in-hospital mortality 0.910 to 0.983, creatinine 0.824 to 0.949. The
  default window takes in the whole prediction date (the hours after the prediction time) and
  every billing diagnosis of the stay. The split between the two is untested.
- **Featurization at equal information** (`day` to `fe`): in-hospital mortality −0.004
  [−0.009, +0.001] and +0.006 (LR). ICU mortality −0.016 and −0.013. Readmission −0.013
  [−0.017, −0.010] and −0.010. Creatinine −0.034 and −0.031. Thrombocytopenia −0.103 and −0.101.
  As in the ladder, the lab tasks lose the most, because FeatureExtraction's range groups say
  whether a value was ever out of range in 30 or 365 days, not what the last value was.
- **The day cutoff** (E3 CDM to `day`): −0.014 and −0.015 (in-hospital mortality), −0.027 and
  −0.031 (ICU mortality), −0.061 and −0.059 (creatinine), −0.029 and −0.029
  (thrombocytopenia). For the lab tasks this includes the pending-lab leak, which the day cutoff
  removes. Readmission gains +0.011 and +0.010, because the end of the discharge date is later
  than the CDM's discharge time.
- **Overall** (E3 CDM to `fe`): in-hospital mortality −0.018 and −0.008, ICU mortality −0.043 and
  −0.044, readmission −0.002 and +0.001, creatinine −0.095 and −0.090, thrombocytopenia −0.132
  and −0.130.
- For E3c (PLP): its default time at risk starts the day after the index, which removes the
  same-day readmissions. Its covariate settings decide the rest. With `endDays` 0 on 24-hour
  tasks, the results would be inflated by +0.07 to +0.13. With `DrugGroupEraOverlapping` and an
  outcome that repeats exposures, the inflation could reach about +0.09.
