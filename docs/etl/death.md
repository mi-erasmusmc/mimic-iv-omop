# death

**Source:** `hosp.admissions.deathtime` (in-hospital deaths), `hosp.patients.dod` (date of
death from hospital and state records, up to one year after the last discharge).
**Code:** `src/mimic_omop/etl/death.py`.

## Conventions

- One record per person who died. The in-hospital `deathtime` is used when there is one (the
  earliest, if several admissions have one); otherwise `dod`.
- `dod` must be used: in the demo it adds 16 deaths after discharge to the 15 in-hospital ones.
  The old OHDSI ETL used `deathtime` only.
- **Decision:** type EHR (32817) for in-hospital deaths, Registry (32879) for deaths known only
  from `dod`; see [review R8](../review.md#r8-type-of-deaths-known-only-from-dod).
- Only persons kept in the CDM (see [person](person.md)).
- Deaths more than 60 days after the last clinical event fall outside observation periods (4 of
  31 in the demo); see [review R1](../review.md#r1-observation-periods-themis-ehr-convention-vs-encounter-based).

## Fields

| Field | Source / logic |
|---|---|
| `person_id` | `subject_id` |
| `death_date` | date of the earliest `admissions.deathtime`, else `patients.dod` |
| `death_datetime` | earliest `admissions.deathtime`; empty for `dod`-only deaths |
| `death_type_concept_id` | 32817 EHR with `deathtime`, 32879 Registry for `dod` only |
| `cause_concept_id` | not populated (MIMIC has no cause of death) |
| `cause_source_value` | not populated |
| `cause_source_concept_id` | not populated |
