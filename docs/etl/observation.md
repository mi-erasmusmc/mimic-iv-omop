# observation

**Source:** coded records whose standard concept is in the Observation domain (see
[general](../general.md#coded-records-are-routed-by-domain)): ICU charted assessments and
documentation (`icu.chartevents`; 367,245 demo rows, including unmapped text-only items, see
[review R22](../review.md#r22-unmapped-chart-items)), `hosp.drgcodes` (unmapped DRGs too), and
diagnosis, HCPCS, lab, ICU input and output codes that map to Observation concepts (e.g. ICU input
item 'PO Intake', mapped to 'Oral intake'), and microbiology tests whose concept is an
Observation (Gram stains, some antigen tests; 191 demo rows, as described for measurement in
[specimen](specimen.md), with the organism, if any, as `value_as_concept_id`), and MIMIC-IV-ED
triage acuity and chief complaint (see below).
**Code:** `src/mimic_omop/etl/events.py` (`drgs` and the other sources), `src/mimic_omop/etl/chart_events.py`, `src/mimic_omop/etl/observation.py`.

## Conventions

- **THEMIS:** a code with a 'Maps to' Observation concept and a 'Maps to value' concept (e.g.
  "Personal history of ..." codes) becomes an observation with that `value_as_concept_id`
  ([records with values](https://ohdsi.github.io/Themis/records_with_values.html)). In the demo,
  333 observations carry such a value.
- DRGs are mapped with CVB by description (`mimiciv_hosp.drgcodes.description`), covering 47% of
  demo DRG rows, and dated at discharge, when they are assigned. Both MS-DRG (`HCFA`) and APR-DRG
  rows are included.
- Diagnosis codes routed here are dated at visit start like other diagnoses.

## Fields

| Field | Source / logic |
|---|---|
| `observation_id` | `prefix * 10^15 + key * 10 + k` (see [general](../general.md#identifiers)); prefix 4 for DRGs, key `hadm_id * 1000 +` the DRG's position in the admission |
| `person_id` | `subject_id` |
| `observation_concept_id` | standard concept of the source code, else 0 |
| `observation_date` | chart, lab, output: date of `charttime`; ICU input: date of `starttime`; DRG: `visit_end_date`; diagnosis: `visit_start_date`; HCPCS: `chartdate` |
| `observation_datetime` | chart, lab, output: `charttime`; ICU input: `starttime`; DRG: `visit_end_datetime` (assigned at discharge; date-only, it would fall after a prediction at discharge); diagnosis: `visit_start_datetime`; else not populated |
| `observation_type_concept_id` | 32817 EHR (chart, output); 32818 EHR administration record (ICU input); 32821 EHR billing record (DRG, diagnosis, HCPCS); 32856 Lab (labs) |
| `value_as_number` | chart, lab, output: the numeric value, as in [measurement](measurement.md); else not populated |
| `value_as_string` | chart: the text `value`, first 60 characters, for text results; else not populated |
| `value_as_concept_id` | 'Maps to value' concept of the source code, if any; chart: CVB mapping of the text value (heart rhythms) |
| `value_as_date` | not populated |
| `qualifier_concept_id` | not populated |
| `unit_concept_id` | chart, lab, output: as in [measurement](measurement.md); else not populated |
| `provider_id` | not populated |
| `visit_occurrence_id` | `hadm_id`, or for labs the assigned visit |
| `visit_detail_id` | chart, lab, output, ICU input: the care unit stay covering the record's time; ICU rows charted up to 6 hours before or 1 hour after their ICU stay (`stay_id`) keep that stay ([R30](../review.md#r30-chart-rows-outside-their-icu-stay)); else not populated |
| `observation_source_value` | DRG: `drg_type:drg_code`; chart, lab, output, ICU input: `itemid`; others: the source code |
| `observation_source_concept_id` | Athena concept of the source code, else CVB's MIMIC4 concept, else 0 |
| `unit_source_value` | chart, lab, output: `valueuom`; else not populated |
| `unit_source_concept_id` | not populated |
| `qualifier_source_value` | not populated |
| `value_source_value` | chart, lab: `value`, first 50 characters; else not populated |
| `value_as_source_concept_id` | not populated |
| `observation_event_id` | not populated (microbiology susceptibilities are measurements) |
| `obs_event_field_concept_id` | not populated |

### ED triage acuity and chief complaint (`ed.triage`, MIMIC-IV-ED)

Two of the triage columns map to Observation concepts in the ETL's local mapping
([measurement](measurement.md#ed-triage-and-vital-signs-edtriage-edvitalsign-mimic-iv-ed),
[review R33](../review.md#r33-mimic-iv-ed)): the Emergency Severity Index (`acuity`, 1-5; LOINC
75636-1) with the level as `value_as_number`, and the chief complaint (LOINC 8661-1) with the
free text as `value_as_string`. In MIMIC-IV 3.1: 418,025 acuity and 424,989 chief complaint
records. The fields are those of a triage value on the measurement page, dated at the ED stay's
`intime`, with `observation_id` `15 * 10^15 + (stay_id * 10 + item) * 10` (acuity 7, chief
complaint 8).

ED diagnoses that map to Observation concepts (143,039, mostly external causes of injury such as
falls and collisions, and status codes such as long-term use of insulin or anticoagulants) are
written here too, as described on
[condition_occurrence](condition_occurrence.md#ed-diagnoses-eddiagnosis-mimic-iv-ed).
