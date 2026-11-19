# measurement

**Source:** coded records whose standard concept is in the Measurement domain (see
[general](../general.md#coded-records-are-routed-by-domain)): labs (`hosp.labevents`), ICU charted
values (`icu.chartevents`), ICU outputs (`icu.outputevents`), outpatient measurements
(`hosp.omr`), microbiology test results and susceptibilities (`hosp.microbiologyevents`, see
[specimen](specimen.md)), and codes of other sources that map to Measurement concepts (e.g. ICD codes for
metastases, which map to Cancer Modifier concepts), and MIMIC-IV-ED triage and vital signs (see
below). Unmapped labs, outputs, omr rows and numeric chart values are written here with concept 0.
**Code:** `src/mimic_omop/etl/events.py` (`labs`), `src/mimic_omop/etl/chart_events.py`,
`src/mimic_omop/etl/omr.py`, `src/mimic_omop/etl/microbiology.py`, `src/mimic_omop/etl/ed.py`,
`src/mimic_omop/etl/measurement.py`.

| Source | Demo rows here | Concept mapped |
|---|---:|---:|
| `labevents` | 107,727 | 96% |
| `chartevents` (numeric, and text items mapped to Measurement) | 327,430 | 82% |
| `outputevents` | 9,341 | 94% |
| `omr` | 3,836 | 100% |
| `microbiologyevents` test results | 1,782 | 92% |
| `microbiologyevents` susceptibilities | 1,036 | 100% |

## Conventions

- Labs: concepts from CVB `mimiciv_hosp.d_labitems.itemid`.
- ICU charted values and outputs: concepts from CVB's ICU item mappings
  (`mimiciv_icu.d_items.label`, `mimiciv_icu.chartevents.itemid`, `mimiciv_icu.outputevents.itemid`),
  or CVB's broader concept for items without an equivalent
  ([review R29](../review.md#r29-cvb-items-mapped-only-to-a-broader-concept)).
  A chart item follows its concept's domain: assessments map to Observation, interventions such as
  pain management or suctioning to Procedure, symptoms to Condition. An unmapped chart item goes
  here when it has a numeric value and to [observation](observation.md) when it has only text
  ([review R22](../review.md#r22-unmapped-chart-items)).
- **Decision:** outpatient measurements (`omr`) use the ETL's only local mapping,
  `src/mimic_omop/mappings/omr.csv`, written as LOINC and UCUM codes and resolved through Athena;
  a blood pressure `120/80` becomes a systolic and a diastolic measurement. See
  [review R21](../review.md#r21-local-mapping-for-omr).
- **Decision:** units from CVB `mimiciv_units`; else an exact UCUM code; else a case-insensitive
  UCUM code where that is unambiguous (MIMIC writes `ml`; UCUM `ms` could be millisecond,
  millisiemens or megasecond, so case is not ignored there). Demo rows with a unit that map: labs
  96%, chart values 99%, outputs 100%.
- **Decision:** a lab result written with an operator and no `valuenum` (`<0.5`, `>= 100`) becomes
  `operator_concept_id` and `value_as_number`.
- Text results stay in `value_source_value`, truncated to the CDM's 50 characters. Lab text
  results get no value concept yet ([review R11](../review.md#r11-lab-text-results-are-not-mapped-to-value-concepts));
  CVB maps a few charted heart rhythm values, which become `value_as_concept_id`.
- Every record has a visit: its admission, else the visit covering its time (or, for date-only
  `omr` rows, its date), else the Laboratory or Outpatient Visit created for that day
  ([visit_occurrence](visit_occurrence.md)). Labs and ICU data are linked to the care unit stay
  covering their time.
- Lab item mappings outside the event domains give concept 0; see
  [review R12](../review.md#r12-targets-outside-the-event-domains).
- Codes of other sources routed here are dated like their source (diagnoses at visit start).

## Fields

| Field | Lab (`labevents`) | ICU (`chartevents`, `outputevents`) | Outpatient (`omr`) |
|---|---|---|---|
| `measurement_id` | `5 * 10^15 + labevent_id * 10 + k` | `9` (charted) or `10` (output) `* 10^15 + (stay_id * 10^6 + seq) * 10 + k`, seq the row's position in its ICU stay | `11 * 10^15 + (subject_id * 10^6 + seq) * 10 + k`, k = 0 systolic, 1 diastolic |
| `person_id` | `subject_id` | `subject_id` | `subject_id` |
| `measurement_concept_id` | CVB target of `itemid`, else 0 | CVB target of `itemid`, else 0 | local mapping of `result_name`, else 0 |
| `measurement_date` | date of `charttime` | date of `charttime` | `chartdate` |
| `measurement_datetime` | `charttime` | `charttime` | not populated (date only) |
| `measurement_time` | not populated (deprecated field) | not populated | not populated |
| `measurement_type_concept_id` | 32856 Lab | 32817 EHR | 32817 EHR |
| `operator_concept_id` | parsed from `value` when `valuenum` is empty: `<` 4171756, `<=` 4171754, `=` 4172703, `>=` 4171755, `>` 4172704 | not populated | not populated |
| `value_as_number` | `valuenum`, else the number after the operator | `valuenum` (charted), `value` (output) | `result_value`, or its systolic / diastolic part |
| `value_as_concept_id` | not populated | CVB mapping of the text `value` (heart rhythms), for text results | not populated |
| `unit_concept_id` | `valueuom`: CVB, else UCUM | `valueuom`: CVB, else UCUM (numeric results only) | UCUM code from the local mapping |
| `range_low` | `ref_range_lower` | not populated | not populated |
| `range_high` | `ref_range_upper` | not populated | not populated |
| `provider_id` | not populated | not populated (`caregiver_id` not used yet) | not populated |
| `visit_occurrence_id` | `hadm_id`, else the visit covering `charttime` | `hadm_id` | the visit covering `chartdate` |
| `visit_detail_id` | the care unit stay covering `charttime` | the care unit stay covering `charttime`; a row charted up to 6 hours before its ICU stay (`stay_id`) gets the stay's first care unit stay, up to 1 hour after it the last ([R30](../review.md#r30-chart-rows-outside-their-icu-stay)) | not populated |
| `measurement_source_value` | `itemid` | `itemid` | `result_name` |
| `measurement_source_concept_id` | CVB's MIMIC4 concept for `itemid` (custom id, [R10](../review.md#r10-cvb-custom-concepts-are-not-in-the-output-vocabulary)) | CVB's MIMIC4 concept for `itemid` | 0 |
| `unit_source_value` | `valueuom`, trimmed | `valueuom`, trimmed | not populated (the unit is part of `result_name`) |
| `unit_source_concept_id` | not populated | not populated | not populated |
| `value_source_value` | `value`, first 50 characters | `value`, first 50 characters (charted) | `result_value` |
| `value_as_source_concept_id` | not populated | not populated | not populated |
| `measurement_event_id` | not populated | not populated | not populated (microbiology: see below) |
| `meas_event_field_concept_id` | not populated | not populated | not populated (microbiology: see below) |

### Microbiology (`microbiologyevents`)

How specimens, test results and susceptibilities fit together is described under
[specimen](specimen.md); the fields that differ from labs:

| Field | Test result | Susceptibility |
|---|---|---|
| `measurement_id` | `12 * 10^15 + microevent_id * 10 + k`; for an isolate known only from its susceptibility rows, the first such row's id with k = 5 | `12 * 10^15 + microevent_id * 10 + k` |
| `measurement_concept_id` | CVB target of `test_itemid` (a culture or assay) | CVB target of `ab_itemid` (the antibiotic's susceptibility concept) |
| `measurement_type_concept_id` | 32856 Lab | 32856 Lab |
| `measurement_date` | date of `charttime`, else `chartdate` | same |
| `measurement_datetime` | `charttime` | same |
| `value_as_concept_id` | the organism (CVB target of `org_itemid`, 0 if unmapped); empty without an organism | S/I/R (CVB `interpretation`: Sensitive, Intermediate, Resistant; Pending); 0 for others (D, N, Z) |
| `value_as_number` | not populated | MIC, `dilution_value` (no unit is recorded) |
| `operator_concept_id` | not populated | `dilution_comparison`: `<=`, `=`, `=>` (as `>=`) |
| `value_source_value` | `org_name`, else `comments` (e.g. `NO GROWTH.`), first 50 characters | `interpretation` |
| `measurement_source_value` | `test_itemid` | `ab_itemid` |
| `measurement_source_concept_id` | CVB's MIMIC4 concept of the test | of the antibiotic |
| `measurement_event_id` | not populated | the isolate's test result |
| `meas_event_field_concept_id` | not populated | the field of that record's id: 1147138 `measurement.measurement_id` (1147165 when it is an observation, 1147082 a procedure) |
| `visit_occurrence_id` | `hadm_id`, else the visit covering `charttime`, else (date only) the visit covering `chartdate` | same |

### ED triage and vital signs (`ed.triage`, `ed.vitalsign`, MIMIC-IV-ED)

Both tables are wide, one column per vital sign; each value is one record. CVB does not map
MIMIC-IV-ED, so the ETL uses a local mapping, `src/mimic_omop/mappings/ed.csv`, written as LOINC
and UCUM codes like the omr mapping ([review R33](../review.md#r33-mimic-iv-ed)): temperature
8310-5 in °F, heart rate 8867-4 /min, respiratory rate 9279-1 /min, SpO2 59408-5 %, systolic and
diastolic blood pressure 8480-6 / 8462-4 mm[Hg], pain 72514-3 (0-10 score), heart rhythm 8884-9
(as text). Triage values are recorded once, at arrival; vital signs through the stay. ESI acuity
and the chief complaint are Observation concepts ([observation](observation.md)). In MIMIC-IV 3.1:
2,843,374 triage and 9,544,495 vital sign measurements, all mapped.

| Field | Triage value | Vital sign value |
|---|---|---|
| `measurement_id` | `15 * 10^15 + (stay_id * 10 + item) * 10`, item the column's position (temperature 0 ... chiefcomplaint 8) | `16 * 10^15 + ((stay_id * 1000 + seq) * 10 + item) * 10`, seq the row's position in the stay by `charttime` |
| `measurement_concept_id` | the column's LOINC concept (mappings/ed.csv) | same |
| `measurement_date`, `measurement_datetime` | the ED stay's `intime` | `charttime` |
| `measurement_type_concept_id` | 32817 EHR | 32817 EHR |
| `value_as_number` | the value, if it is a number (pain 'unable' or 'sleeping' is not) | same; none for `rhythm` |
| `unit_concept_id` | the column's UCUM unit | same |
| `visit_occurrence_id` | the visit of the ED stay's transfer | same |
| `visit_detail_id` | the ED transfer the stay starts with | the care unit stay covering `charttime` in that visit |
| `measurement_source_value` | the column name (`heartrate`, `sbp`, ...) | same |
| `value_source_value` | the value as recorded | same |
