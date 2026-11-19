# Data quality: DQD and Achilles

How to run them, and what the Data Quality Dashboard reports on the MIMIC-IV demo.

## Running

```sh
uv sync --extra duckdb
uv run mimic-omop export-duckdb data/demo-cdm data/demo-cdm.duckdb --memory-limit 8GB
DUCKDB_MEMORY_LIMIT=10GB scripts/capped.sh 16G Rscript scripts/run_dqd_achilles.R data/demo-cdm.duckdb data/dqd
uv run python scripts/dqd_summary.py data/dqd/dqd/dqd.json
```

**Memory.** DuckDB's default memory limit is 80% of the machine's memory, regardless of what else
is running; on the full MIMIC-IV that got DQD killed by the kernel at 31 GB. Both the export
(`--memory-limit`) and the R script (`DUCKDB_MEMORY_LIMIT`) set an explicit limit, beyond which
DuckDB spills to a temporary directory. `scripts/capped.sh <limit> <command>` additionally runs a
command in a memory-capped systemd scope, so if it still overshoots only that command is stopped,
not whatever process the kernel's out-of-memory killer would pick.

`export-duckdb` creates every table of the CDM specification (empty ones included, as the tools
expect) in a DuckDB file written in storage version v1.2.0, so DuckDB in R can open it. Integer
fields are 32-bit, as the CDM specifies; ids that do not fit are renumbered
([Identifiers](general.md#identifiers)). The R
script needs DatabaseConnector, duckdb, Achilles and DataQualityDashboard; it runs Achilles with
CDM version 5.4 (Achilles knows no 5.5, which only adds fields and tables) and DQD with 5.5. It
contains a workaround for an incompatibility between Achilles / DatabaseConnector and the duckdb
R package (case-sensitive column names when inserting), described in the script. View the
dashboard with `DataQualityDashboard::viewDqDashboard("data/dqd/dqd/dqd.json")`.

## Results on the demo

DataQualityDashboard 2.9.1 and Achilles 1.7.2, on the demo CDM built by this version of the ETL:
**2,359 of 2,409 checks pass (97.9% of the applicable checks), 0 errors**. All 50 failures are
explained, and none is an ETL defect:

| Cause | Failed checks | Notes |
|---|---:|---|
| Dates in the future | 34 | `plausibleValueHigh` on every date field and year of birth: MIMIC dates are shifted into 2100–2200. Expected for MIMIC. |
| Mapping coverage | 8 | `standardConceptRecordCompleteness`, `sourceConceptRecordCompleteness`, `sourceValueCompleteness` for drug_exposure (14% concept 0, ICU inputs: [review R19](review.md#r19-icu-input-items-without-a-mapping-and-route)), measurement (21%) and observation (83%, nursing documentation: [R22](review.md#r22-unmapped-chart-items)). |
| Home and no transfer are 0 | 2 | `admitted_from_concept_id` and `discharged_to_concept_id` are 0 for most visits. The CDM prescribes 0 for admission from and discharge to home, and outpatient, laboratory and ED-only visits have no transfer; see [R25](review.md#r25-discharge-to-home-is-concept-0). |
| Lab units | 3 | `plausibleUnitConceptIds`: MCHC (item 51249) is reported in both g/dL and % in MIMIC; RDW-SD (item 52172, fL) is mapped by CVB to an RDW concept measured in % ([R26](review.md#r26-feedback-for-cvb)); epithelial cells per high power field have a unit DQD does not list for that concept. |
| Sex-specific conditions | 2 | `plausibleGender`: 'Postmenopausal state' recorded for 5 male rows, in the source data. |
| Athena content | 1 | `fkClass` on `drug_strength.ingredient_concept_id` (0.12% of rows), in Athena's own table. |

## Results on the full MIMIC-IV 3.1

The same versions, on the CDM of the full MIMIC-IV 3.1 with MIMIC-IV-ED (last rebuilt 2026-11-19:
364,627 persons, 406M measurements, 216M observations, 69M drug exposures, 9.6M
procedures, 1.9M specimens, 2,892 episodes, 537k payer plan periods): **2,350 of 2,409 checks
pass (97.6% of the applicable checks), 0 errors**. The export took 97 s and 58 GB; Achilles and DQD
together 3 min and 38 GB, with DuckDB limited to 64 GB.

| Cause | Failed checks | Notes |
|---|---:|---|
| Dates in the future | 42 | as on the demo, now also for episode, payer_plan_period, procedure end dates and specimen |
| Mapping coverage | 10 | concept 0 in 7.9% of drug_exposure, 14% of measurement, 70% of observation (nursing documentation, [R22](review.md#r22-unmapped-chart-items)), 10.2% of procedure_occurrence (ICU line cap, tubing and dressing changes, which CVB maps only post-coordinated: [R34](review.md#r34-icu-dates-dated-when-they-happened)) and 5.2% of specimen (MRSA SCREEN and three other specimen types CVB does not map); and source value and source concept completeness for the same tables (procedure_occurrence just over its 10% threshold) |
| Home and no transfer are 0 | 2 | 88% and 96% of visits ([R25](review.md#r25-discharge-to-home-is-concept-0)); most visits are outpatient and laboratory visits |
| Lab units | 3 | the same three concepts as on the demo (RDW, MCHC, urine epithelial cells), 0.6M to 2.3M rows each |
| Sex-specific conditions | 1 | one record of a sex-specific condition in the other sex |
| Athena content | 1 | as on the demo |

Compared with the run of 2026-10-28 (49 failures), 11 checks fail that passed then and one
passes that failed then (drug_exposure source concept completeness, after the uphill mappings of
[R29](review.md#r29-cvb-items-mapped-only-to-a-broader-concept)). Of the 11, 8 are date checks
of the fields and tables added since, and 2 the concept completeness of the new ICU dates and
microbiology specimens. The last, the observation unit, exposes a wrong mapping: CVB records ICU
item 229661 'Compliance' (lung compliance, charted in cmH2O/L/seconds) as `Is a` 'Compliance
behavior to treatment' (606892, adherence), so since R29 its values are observations of
adherence instead of unmapped measurements. It led to a review of all such mappings
([uphill-review](uphill-review.md)) and to an exclusion list of wrong CVB relationships (general
rule 9), fed back to CVB ([R26](review.md#r26-feedback-for-cvb)). With the 9 exclusions, rerun the
same day: still 2,350 of 2,409 checks pass. The observation unit check passes; procedure source
concept completeness now just fails (10.16%, threshold 10%), as the 511,665 'Placement Confirmed'
chartings are no longer procedures and the unmapped ICU dates weigh more. The failures listed
above are of that run; all are explained by the source or by CVB's coverage, none by an ETL
defect.

The 19 persons whose recorded death precedes all their care
([R28](review.md#r28-death-recorded-before-a-persons-care)) stay below DQD's thresholds for
events after death.

Achilles results are in the `results` schema of the database and in `data/dqd/achilles`.
