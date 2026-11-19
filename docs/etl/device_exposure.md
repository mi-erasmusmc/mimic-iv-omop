# device_exposure

**Source:** coded records whose standard concept is in the Device domain (see
[general](../general.md#coded-records-are-routed-by-domain)). In the demo these are ICU inputs of
blood products (platelets, fresh frozen plasma) and enteral nutrition (Glucerna), which Athena
places in the Device domain, a few NDC-coded products from orders and eMAR (contrast agent,
skin cream), and ICU chart items mapped to devices (285 rows); 551 rows in all.
**Code:** `src/mimic_omop/etl/device_exposure.py`; the records are built by the drug sources in
`src/mimic_omop/etl/drug_events.py` (see [drug_exposure](drug_exposure.md)) and by
`src/mimic_omop/etl/chart_events.py`.

## Conventions

- Dates, visits, care units and type concepts are those of the source record, as described in
  [drug_exposure](drug_exposure.md).
- Device records are only those whose source code maps to a Device concept; a source has no
  unmapped records here (unmapped orders and administrations go to drug_exposure).

## Fields

| Field | Source / logic |
|---|---|
| `device_exposure_id` | the id the record would have in drug_exposure (see there) |
| `person_id` | `subject_id` |
| `device_concept_id` | the standard Device concept of the source code |
| `device_exposure_start_date` | as `drug_exposure_start_date` |
| `device_exposure_start_datetime` | as `drug_exposure_start_datetime` |
| `device_exposure_end_date` | as `drug_exposure_end_date` |
| `device_exposure_end_datetime` | as `drug_exposure_end_datetime` |
| `device_type_concept_id` | 32833 EHR order or 32818 EHR administration record |
| `unique_device_id` | not populated |
| `production_id` | not populated |
| `quantity` | not populated |
| `provider_id` | not populated |
| `visit_occurrence_id` | as in drug_exposure |
| `visit_detail_id` | as in drug_exposure |
| `device_source_value` | as `drug_source_value` |
| `device_source_concept_id` | as `drug_source_concept_id` |
| `unit_concept_id` | not populated |
| `unit_source_value` | not populated |
| `unit_source_concept_id` | not populated |
