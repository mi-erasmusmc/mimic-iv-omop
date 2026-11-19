# visit_occurrence

**Source:** `hosp.admissions` (one visit per admission), `hosp.transfers` (visit boundaries and
ED-only stays), `hosp.omr` (outpatient measurement days), `hosp.labevents` (labs outside every
other visit).
**Code:** `src/mimic_omop/etl/visit_occurrence.py`.

## Four kinds of visit

| Kind | Source | visit_concept_id | Demo |
|---|---|---|---:|
| Admission | `admissions` row | 262, 9201 or 581385 (below) | 275 |
| ED stay without admission | `transfers` rows with no `hadm_id`; contiguous rows are one stay | 9203 Emergency Room Visit | 54 |
| Outpatient Visit | days with `omr` measurements or microbiology specimens dated by day only, outside every admission and ED stay, one per person and day | 9202 Outpatient Visit | 835 |
| Laboratory Visit | labs and microbiology specimens without `hadm_id` outside every other visit, one per person and day | 32036 Laboratory Visit | 197 |

Admission concepts:

- **CDM:** an admission that started in the ED (`edregtime` present) is Emergency Room and
  Inpatient Visit (262), "Person visiting ER followed by a subsequent Inpatient Visit".
- **Decision:** admission types `EU OBSERVATION` and `AMBULATORY OBSERVATION` are Observation
  Room (581385), whether or not they came through the ED; `OBSERVATION ADMIT` and `DIRECT
  OBSERVATION`, inpatient stays in all but billing status, are typed like other admissions. See
  [review R3](../review.md#r3-observation-admissions-with-icu-stays).
- All other admissions are Inpatient Visit (9201), including `SURGICAL SAME DAY ADMISSION`.

## Conventions

- **CDM:** "Multi-day visits must not overlap, i.e. share days other than start and end days".
  Checked on the demo by the tests.
- **Decision:** an admission's visit spans `admittime` to `dischtime`, widened to cover all its
  transfers, and its ED part comes from its ED transfers rather than `edregtime`. See
  [review R13](../review.md#r13-visit-boundaries).
- **Decision:** days with outpatient measurements get Outpatient Visits, and remaining
  outpatient labs Laboratory Visits; see
  [review R23](../review.md#r23-outpatient-visits-for-omr-days) and
  [review R4](../review.md#r4-laboratory-visits-for-labs-without-an-admission).
- ED-only stays take their arrival and disposition from MIMIC-IV-ED, when it is converted
  ([review R33](../review.md#r33-mimic-iv-ed)): the first ED stay's `arrival_transport` as
  `admitted_from`, the last one's `disposition` as `discharged_to` (ED stays registered at the
  same time are ordered by `outtime` and `stay_id`). Of 408,872 ED-only visits in MIMIC-IV 3.1,
  221,963 have an ED stay in MIMIC-IV-ED, which covers 2011-2019 only; 50,831 arrived by
  ambulance or helicopter and 2,329 were transferred to another hospital. Walk-in and the other
  arrivals, and the other dispositions (home, left without being seen, eloped, against medical
  advice, expired, ...), have no Visit concept and get 0; a death is in [death](death.md).
  Admissions keep their own `admission_location` and `discharge_location`.
- The visit end is never before its start (a guard; no demo admission is discharged before it
  starts).
- Following the CDM's description of `visit_source_value` as the kind of visit, it holds the
  admission type, not the `hadm_id` (which is the `visit_occurrence_id`). The old OHDSI ETL
  stored `subject_id|hadm_id` there.

## Fields

| Field | Admission | ED stay | Outpatient Visit | Laboratory Visit |
|---|---|---|---|---|
| `visit_occurrence_id` | `hadm_id` | first `transfer_id` of the stay | `2 * 10^12 + person_id * 10^5 + days since 1970-01-01` | `10^12 + person_id * 10^5 + days since 1970-01-01` |
| `person_id` | `subject_id` | `subject_id` | `subject_id` | `subject_id` |
| `visit_concept_id` | 262, 9201 or 581385, see above | 9203 | 9202 | 32036 |
| `visit_start_date` | date of `visit_start_datetime` | same | the `omr` `chartdate` | same as datetime |
| `visit_start_datetime` | earliest of `admittime` and the first transfer's `intime` (`edregtime` if the admission has no transfers) | first `intime` | 00:00 of the day | first lab `charttime` of the day |
| `visit_end_date` | date of `visit_end_datetime` | same | the `omr` `chartdate` | same as datetime |
| `visit_end_datetime` | latest of `dischtime` and the last transfer's `outtime`, not before the start | last `outtime` | 23:59:59.999999 of the day | last lab `charttime` of the day |
| `visit_type_concept_id` | 32817 EHR | 32817 EHR | 32817 EHR | 32817 EHR |
| `provider_id` | not populated (`admit_provider_id` not used yet) | not populated | not populated | not populated |
| `care_site_id` | not populated (care units are on visit_detail) | not populated | not populated | not populated |
| `visit_source_value` | `admission_type` | `ED` | the source tables of the day's records: `omr`, `microbiologyevents` or `microbiologyevents+omr` | `labevents`, `microbiologyevents` or `labevents+microbiologyevents` |
| `visit_source_concept_id` | 0 | 0 | 0 | 0 |
| `admitted_from_concept_id` | CVB `mimiciv_hosp.admissions.admission_location`, Visit-domain targets; else 0 | MIMIC-IV-ED `arrival_transport`: AMBULANCE 8668 Ambulance - Land, HELICOPTER 8850 Ambulance - Air or Water; else 0 | 0 | 0 |
| `admitted_from_source_value` | `admission_location` | `arrival_transport` of the stay's first ED stay, if MIMIC-IV-ED has it | empty | empty |
| `discharged_to_concept_id` | CVB `mimiciv_hosp.admissions.discharge_location`, Visit-domain targets; 0 for `HOME` ([R25](../review.md#r25-discharge-to-home-is-concept-0)) and unmapped locations | MIMIC-IV-ED `disposition` through the same CVB mapping (TRANSFER as ACUTE HOSPITAL: 38004279); HOME and the others 0 | 0 | 0 |
| `discharged_to_source_value` | `discharge_location` | `disposition` of the stay's last ED stay, if MIMIC-IV-ED has it | empty | empty |
| `preceding_visit_occurrence_id` | the person's previous visit of any kind by start time | same | same | same |
