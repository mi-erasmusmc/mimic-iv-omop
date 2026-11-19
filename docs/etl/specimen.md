# specimen

**Source:** `hosp.microbiologyevents`, one record per `micro_specimen_id` (1,336 in the demo,
1,924,289 in MIMIC-IV 3.1),
and coded records of other sources whose standard concept is in the Specimen domain (none in
MIMIC-IV 3.1).
**Code:** `src/mimic_omop/etl/specimen.py`, `src/mimic_omop/etl/microbiology.py`.

## Microbiology in the CDM

OHDSI has no settled convention for microbiology (see
[review R31](../review.md#r31-microbiology-without-a-settled-convention)); the ETL uses the
CDM's own fields:

| MIMIC | CDM |
|---|---|
| a specimen (`micro_specimen_id`, `spec_itemid`) | a specimen record |
| a test result (a row without an antibiotic): the test, and the organism found or the comment | a record of the test's concept, normally in measurement, with the organism as `value_as_concept_id` (or the comment, e.g. "NO GROWTH.", as `value_source_value`); linked to its specimen in [fact_relationship](fact_relationship.md) |
| an isolate tested for antibiotics (MIMIC has no row of its own) | the same kind of test result record, derived from its first susceptibility row |
| a susceptibility (a row with an antibiotic) | a measurement of the antibiotic's susceptibility concept, S/I/R as `value_as_concept_id`, the MIC as `value_as_number` and `operator_concept_id`, and `measurement_event_id` pointing at its isolate's test result (see [measurement](measurement.md)) |

So a query for, say, blood cultures growing *E. coli* finds measurements of the blood culture
concept with `value_as_concept_id` E. coli; the specimen and the susceptibilities are one join
away.

In MIMIC-IV 3.1 (all 3,988,224 rows accounted for, [evaluation E1](../evaluation-results.md#e1-conservation)):

| Record | Rows | Concept mapped | Value mapped |
|---|---:|---:|---:|
| specimens | 1,924,289 | 94.8% | |
| test results in measurement | 2,512,740 (153,019 derived isolates) | 93.0% | organism on 378,049, mapped 99.8% |
| test results in observation (Gram stains, antigens) | 196,223 | 100% | |
| test results in procedure_occurrence | 22,150 | 100% | (no value field) |
| susceptibilities | 1,410,258 | 99.9% | S/I/R 99.98%; MIC on 1,373,748 |
| fact_relationship rows | 5,462,226 (two per test result) | | |

## Conventions

- `specimen_concept_id` from CVB `mimiciv_hosp.microbiologyevents.spec_itemid`, Specimen-domain
  targets only (CVB maps a few specimen types, such as MRSA screens, to tests); 90.7% of demo
  and 94.8% of MIMIC-IV 3.1 specimens have a concept.
- Dates: `charttime`, or for specimens with only a `chartdate` (3.4% in the demo, 7.6% of
  MIMIC-IV 3.1 rows, all outpatient) the date alone.
- Visits: the admission (`hadm_id`), else the visit covering `charttime`; specimens taken
  outside every visit get a Laboratory Visit like outpatient labs, and date-only ones an
  Outpatient Visit for the day (see [visit_occurrence](visit_occurrence.md)). In MIMIC-IV 3.1
  this adds 71,507 visits (50,138 Laboratory, 21,369 Outpatient), 60,035 net: 11,484 days of
  outpatient labs now fall in the Outpatient Visit of a date-only specimen instead of a
  Laboratory Visit of their own.
- Quantity, anatomic site and disease status are not recorded in MIMIC.

## Fields

| Field | Source / logic |
|---|---|
| `specimen_id` | `micro_specimen_id`; coded records of other sources: their event id (see [general](../general.md#identifiers)) |
| `person_id` | `subject_id` |
| `specimen_concept_id` | CVB target of `spec_itemid` in the Specimen domain, else 0 |
| `specimen_type_concept_id` | 32856 Lab |
| `specimen_date` | date of `charttime`, else `chartdate` |
| `specimen_datetime` | `charttime` |
| `quantity` | not populated |
| `unit_concept_id` | not populated |
| `anatomic_site_concept_id` | not populated |
| `disease_status_concept_id` | not populated |
| `specimen_source_id` | `micro_specimen_id` |
| `specimen_source_value` | `spec_type_desc` (e.g. `BLOOD CULTURE`, `URINE`) |
| `unit_source_value` | not populated |
| `anatomic_site_source_value` | not populated |
| `disease_status_source_value` | not populated |
| `visit_occurrence_id` | `hadm_id`, else the visit covering `charttime`, else (date only) the visit covering `chartdate` |
| `visit_detail_id` | the care unit stay covering `charttime` |
