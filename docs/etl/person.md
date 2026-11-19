# person

**Source:** `hosp.patients` (one row per `subject_id`), `hosp.admissions.race`.
**Code:** `src/mimic_omop/etl/person.py`.

## Conventions

- One person per `subject_id`; `person_id = subject_id`.
- **CDM / THEMIS:** persons without any clinical event, and so without an observation period,
  are excluded ([person exclusion](https://ohdsi.github.io/Themis/excluded_persons.html)). None in
  the demo. See [review R14](../review.md#r14-persons-without-clinical-events-are-excluded).
- **Decision:** year of birth is `anchor_year - anchor_age`. See
  [review R15](../review.md#r15-year-of-birth).
- **Decision:** race and ethnicity come from the latest admission with an informative `race`
  string; see [review R7](../review.md#r7-race-and-ethnicity-from-a-combined-source-string).

## Fields

| Field | Source / logic |
|---|---|
| `person_id` | `subject_id` |
| `gender_concept_id` | `gender`: M → 8507 MALE, F → 8532 FEMALE, else 0 |
| `year_of_birth` | `anchor_year - anchor_age` |
| `month_of_birth` | not populated (not in MIMIC) |
| `day_of_birth` | not populated (not in MIMIC) |
| `birth_datetime` | not populated (not in MIMIC) |
| `race_concept_id` | CVB `mimiciv_hosp.admissions.race`, Race-domain targets only; else 8527/8516/8515 from a WHITE/BLACK/ASIAN prefix; else 0 |
| `ethnicity_concept_id` | 38003563 Hispanic for `HISPANIC*` and `SOUTH AMERICAN`; 0 for UNKNOWN, UNABLE TO OBTAIN, PATIENT DECLINED TO ANSWER, OTHER, MULTIPLE RACE/ETHNICITY or no string; else 38003564 Not Hispanic |
| `location_id` | not populated (MIMIC has no location) |
| `provider_id` | not populated |
| `care_site_id` | not populated |
| `person_source_value` | `subject_id` as text |
| `gender_source_value` | `gender` |
| `gender_source_concept_id` | 0 |
| `race_source_value` | the chosen `race` string |
| `race_source_concept_id` | CVB's MIMIC4 source concept for the string (custom id, see [R10](../review.md#r10-cvb-custom-concepts-are-not-in-the-output-vocabulary)), else 0 |
| `ethnicity_source_value` | the chosen `race` string (the same source string) |
| `ethnicity_source_concept_id` | 0 |
