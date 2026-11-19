# condition_era

**Source:** [condition_occurrence](condition_occurrence.md).
**Code:** `src/mimic_omop/etl/eras.py`.

## Conventions

The standard OHDSI condition era logic:

- Records of the same person and condition concept are combined into one era when a record starts
  within 30 days of the end of the previous one.
- A record without an end date counts as one day (start + 1 day). MIMIC diagnoses have no end
  date, so each diagnosis is a one-day record, dated at the start of its visit
  ([review R5](../review.md#r5-diagnoses-are-dated-at-the-start-of-their-visit)).
- Records with concept 0 are left out.

## Fields

| Field | Source / logic |
|---|---|
| `condition_era_id` | `person_id * 10^6 +` the era's position for that person (by concept, then start) |
| `person_id` | `person_id` |
| `condition_concept_id` | `condition_concept_id` |
| `condition_era_start_date` | earliest `condition_start_date` of the era |
| `condition_era_end_date` | latest end of the era's records (`condition_end_date`, else start + 1 day) |
| `condition_occurrence_count` | number of records in the era |
