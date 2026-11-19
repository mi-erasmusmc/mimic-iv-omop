# condition_occurrence

**Source:** coded records whose standard concept is in the Condition domain (see
[general](../general.md#coded-records-are-routed-by-domain)): mainly `hosp.diagnoses_icd`, plus
any procedure, HCPCS or lab code that maps to a Condition concept. Unmapped diagnoses are written
here with concept 0. MIMIC-IV-ED's own diagnoses (`ed.diagnosis`) are written here too (see
below).
**Code:** `src/mimic_omop/etl/events.py` (`diagnoses`), `src/mimic_omop/etl/ed.py` (`diagnoses`),
`src/mimic_omop/etl/condition_occurrence.py`.

## Conventions

- ICD-9-CM and ICD-10-CM codes (by `icd_version`) are mapped with Athena, CVB filling codes
  Athena does not map ([general](../general.md#vocabularies-and-mapping)). In the demo, 16% of
  diagnosis rows map to Observation concepts (history, status and external cause codes) and are
  written to [observation](observation.md) instead; about 1% each go to procedure_occurrence and
  measurement (metastasis codes map to Cancer Modifier concepts in the Measurement domain).
  Every demo condition record has a standard concept. ICD-10 Y92 place-of-occurrence codes,
  which Athena deliberately leaves without a standard mapping, get CVB's broader "Place of
  occurrence of injury" concepts and go to observation
  ([review R29](../review.md#r29-cvb-items-mapped-only-to-a-broader-concept)).
- **Decision:** diagnoses are dated at the start of their visit. See
  [review R5](../review.md#r5-diagnoses-are-dated-at-the-start-of-their-visit): they are known only
  at discharge.
- **Decision:** condition status from `seq_num`; see
  [review R6](../review.md#r6-condition-status-from-the-diagnosis-sequence-number).
- `condition_end_date` is left empty: MIMIC records when a diagnosis was coded, not when the
  condition ended.
- ICU chart items that CVB maps to Condition concepts (symptoms such as headache or difficulty
  swallowing; 723 demo rows) are written here too, dated by `charttime`, typed EHR (32817) and
  linked to their care unit stay; their source value is the `itemid`.
- ICU dates on which a pressure ulcer or skin impairment was identified (`icu.datetimeevents`)
  map to 'Impaired skin integrity' and are written here, one per patient, item and date, dated
  at that date (83,742 in MIMIC-IV 3.1; see
  [procedure_occurrence](procedure_occurrence.md#icu-dates-datetimeevents)).

## Fields (for `diagnoses_icd`)

| Field | Source / logic |
|---|---|
| `condition_occurrence_id` | `1 * 10^15 + ((hadm_id * 1000 + seq_num) * 10 + icd_version - 9) * 10 + k`; the ICD version is part of the key because 12 admissions are coded in both ICD-9 and ICD-10 with the same `seq_num` |
| `person_id` | `subject_id` |
| `condition_concept_id` | standard concept of `icd_code` ('Maps to'), else 0 |
| `condition_start_date` | `visit_start_date` of the admission |
| `condition_start_datetime` | `visit_start_datetime` of the admission |
| `condition_end_date` | not populated |
| `condition_end_datetime` | not populated |
| `condition_type_concept_id` | 32821 EHR billing record |
| `condition_status_concept_id` | `seq_num = 1` → 32902 Primary diagnosis, else 32908 Secondary diagnosis |
| `stop_reason` | not populated |
| `provider_id` | not populated |
| `visit_occurrence_id` | `hadm_id` |
| `visit_detail_id` | not populated (diagnoses have no time within the admission) |
| `condition_source_value` | `icd_code` as in MIMIC (without dots) |
| `condition_source_concept_id` | Athena concept of the ICD code, else CVB's MIMIC4 concept, else 0 |
| `condition_status_source_value` | `seq_num` |

### ED diagnoses (`ed.diagnosis`, MIMIC-IV-ED)

The diagnoses coded for an ED stay, separately from the admission's
([review R33](../review.md#r33-mimic-iv-ed)); both are kept. They are mapped like `diagnoses_icd`
(Athena, CVB for codes Athena does not map), so codes can land in other tables too: of 898,874
MIMIC-IV 3.1 rows (176 of the 76 stays of patients not in MIMIC-IV are left out), 794,417 are
conditions, 143,039 observations, 5,860 measurements, 3,499 procedures and 20 episodes, all with a
standard concept but 7 rows. The fields that differ from `diagnoses_icd`:

| Field | ED diagnosis |
|---|---|
| `condition_occurrence_id` | `14 * 10^15 + ((stay_id * 1000 + seq_num) * 10 + icd_version - 9) * 10 + k` |
| `condition_start_date` | date of the ED stay's `intime` |
| `condition_start_datetime` | the ED stay's `intime` |
| `condition_type_concept_id` | 32821 EHR billing record |
| `condition_status_concept_id` | 32902 Primary diagnosis for `seq_num` 1, else 32908 Secondary diagnosis |
| `visit_occurrence_id` | the visit of the ED stay's transfer: an ED-only visit or the admission it led to |
| `visit_detail_id` | the ED transfer the stay starts with |
| `condition_source_value` | `icd_code` |
