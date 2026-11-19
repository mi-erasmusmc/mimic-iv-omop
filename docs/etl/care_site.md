# care_site

**Source:** distinct `hosp.transfers.careunit` values (other than discharge events).
**Code:** `src/mimic_omop/etl/care_site.py`. Built once, not per person bucket.

## Conventions

- **CDM:** "If the source ... only provides limited information such as Place of Service,
  generic or 'pooled' Care Site records are listed". MIMIC names hospital units only, so each
  unit name is one care site.
- **Decision:** `care_site_id` is derived from the unit name (first 31 bits of SHA-256), so it is
  the same in the demo and the full data and across runs. See
  [review R16](../review.md#r16-care-sites).

## Fields

| Field | Source / logic |
|---|---|
| `care_site_id` | SHA-256 of `careunit`, first 31 bits |
| `care_site_name` | `careunit` |
| `place_of_service_concept_id` | CVB `mimiciv_hosp.transfers.careunit`, Visit-domain targets; else 0 (4 of 31 mapped in the demo) |
| `location_id` | not populated |
| `care_site_source_value` | `careunit` |
| `place_of_service_source_value` | `careunit` |
