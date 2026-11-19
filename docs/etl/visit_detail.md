# visit_detail

**Source:** `hosp.transfers`, every row other than `eventtype = 'discharge'` (a discharge row
marks the end of the last stay and has no duration).
**Code:** `src/mimic_omop/etl/visit_detail.py`.

## Conventions

- **CDM:** the table holds "the movement between units in a hospital during an inpatient stay";
  each transfer row is one stay in one care unit.
- Each detail belongs to its admission's visit, or for stays without an `hadm_id` to the ED visit
  built from them (see [visit_occurrence](visit_occurrence.md)). Every detail lies inside its
  visit, because admission visits are widened to cover their transfers.
- **Decision:** the concept is chosen by care unit name: units starting with `Emergency
  Department` are ER (9203); names containing `Intensive Care`, `ICU` or `(CCU)` are Intensive
  Care (32037); `Observation` is Observation Room (581385); all others, including step-down,
  PACU and discharge lounge, are Inpatient (9201).
- **Deviation:** the CDM asks that the detail concept descend from the visit concept. With
  Emergency Room and Inpatient Visit (262) having no descendants, and Observation Room under
  Outpatient Visit, this cannot hold for most details of those visits; every run reports the
  count. See [review R2](../review.md#r2-visit-detail-concepts-are-not-always-descendants-of-the-visit-concept).
- ICU stays are identified from unit names, not from `icu.icustays`; MIMIC's `icustays` merges
  consecutive ICU transfers and also counts some step-down units.

## Fields

| Field | Source / logic |
|---|---|
| `visit_detail_id` | `transfer_id` |
| `person_id` | `subject_id` |
| `visit_detail_concept_id` | from `careunit`, see above |
| `visit_detail_start_date` | date of `intime` |
| `visit_detail_start_datetime` | `intime` |
| `visit_detail_end_date` | date of `visit_detail_end_datetime` |
| `visit_detail_end_datetime` | `outtime`, not before `intime`; a stay without an `outtime` (103 in MIMIC-IV 3.1) ends at the next transfer's `intime` in its admission, else at its own `intime` |
| `visit_detail_type_concept_id` | 32817 EHR |
| `provider_id` | not populated |
| `care_site_id` | the [care_site](care_site.md) of `careunit` |
| `visit_detail_source_value` | `careunit` |
| `visit_detail_source_concept_id` | 0 |
| `admitted_from_concept_id` | not populated |
| `admitted_from_source_value` | not populated |
| `discharged_to_source_value` | not populated |
| `discharged_to_concept_id` | not populated |
| `preceding_visit_detail_id` | the person's previous stay by `intime`, also in an earlier visit (CDM: the detail "that occurred for the person prior to the given visit detail record") |
| `parent_visit_detail_id` | not populated |
| `visit_occurrence_id` | `hadm_id`; for stays without one, their ED visit |
