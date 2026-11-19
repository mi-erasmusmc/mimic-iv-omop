# payer_plan_period

**Source:** `hosp.admissions.insurance`.
**Code:** `src/mimic_omop/etl/payer_plan_period.py`, `src/mimic_omop/mappings/payer.csv`.

## Conventions

- MIMIC records the patient's insurance per admission: Medicare, Medicaid, Private, Other or No
  charge (empty for 9,355 of 546,028 MIMIC-IV 3.1 admissions). It says nothing about coverage
  between admissions, so each admission with an insurance is one period, from the admission to
  the discharge date: 536,673 periods. 9% of patients have more than one insurance across their
  admissions.
- **CDM:** `payer_concept_id` is a concept of the Payer domain, from the Source of Payment
  Typology (SOPT). CVB maps insurance to LOINC answers, which are not in that domain, so the
  ETL uses a small local mapping in SOPT codes, resolved through Athena: Medicare 1 (280),
  Medicaid 2 (289), Private 5 Private Health Insurance (327), No charge 82 (437). 'Other' has
  no SOPT equivalent and gets 0 with its source value. *(Decision; propose to CVB with R26.)*
- MIMIC gives no plan, sponsor, family or stop reason.

## Fields

| Field | Source / logic |
|---|---|
| `payer_plan_period_id` | `hadm_id` |
| `person_id` | `subject_id` |
| `payer_plan_period_start_date` | date of `admittime` |
| `payer_plan_period_end_date` | date of `dischtime` (never before the start) |
| `payer_concept_id` | SOPT concept of `insurance` (mappings/payer.csv), else 0 |
| `payer_source_value` | `insurance` |
| `payer_source_concept_id` | 0 |
| `plan_concept_id` | not populated |
| `plan_source_value` | not populated |
| `plan_source_concept_id` | not populated |
| `sponsor_concept_id` | not populated |
| `sponsor_source_value` | not populated |
| `sponsor_source_concept_id` | not populated |
| `family_source_value` | not populated |
| `stop_reason_concept_id` | not populated |
| `stop_reason_source_value` | not populated |
| `stop_reason_source_concept_id` | not populated |
