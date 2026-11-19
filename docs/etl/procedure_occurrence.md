# procedure_occurrence

**Source:** coded records whose standard concept is in the Procedure domain (see
[general](../general.md#coded-records-are-routed-by-domain)): mainly `hosp.procedures_icd` and
`hosp.hcpcsevents`, ICU procedures (`icu.procedureevents`) and ICU dates of line insertions and
line care (`icu.datetimeevents`), plus diagnosis codes, ICU chart
items and microbiology tests that map to Procedure concepts. Unmapped procedure, HCPCS, ICU
procedure and ICU date items are written here with concept 0.
**Code:** `src/mimic_omop/etl/events.py` (`procedures`, `hcpcs`), `src/mimic_omop/etl/chart_events.py`
(`procedures`, `dated`), `src/mimic_omop/etl/procedure_occurrence.py`.

## Conventions

- ICD-9-Proc / ICD-10-PCS codes by `icd_version`; `hcpcs_cd` is CPT4 when it is five digits or
  four digits and a letter, HCPCS otherwise. Mapped with Athena, CVB filling gaps.
- Procedures are dated by `chartdate`; MIMIC gives no time of day.
- Diagnosis codes routed here are dated like diagnoses (visit start), see
  [condition_occurrence](condition_occurrence.md).
- ICU chart items that CVB maps to Procedure concepts (care interventions such as pain management,
  suctioning or delirium assessment; 10,880 demo rows) are written here too, dated by
  `charttime`, typed EHR (32817) and linked to their care unit stay; their source value is the
  `itemid`.
- ICU procedures (`icu.procedureevents`: ventilation, dialysis, lines, intubation, imaging, EKG)
  use CVB's `mimiciv_icu.procedureevents.itemid` mapping, and its ICU item labels for the items
  that set lacks. They keep their start and end (`starttime`, `endtime`), are typed EHR (32817)
  and linked to their care unit stay as other ICU rows are
  ([review R30](../review.md#r30-chart-rows-outside-their-icu-stay)). In the demo, 1,250 of
  1,468 rows are procedures (96.6% mapped); cultures sent map to Measurement concepts (94) and
  transfers to and from the OR, family meetings and similar to Observation concepts (124). In
  MIMIC-IV 3.1: 690,921 procedures (95.5% mapped), 53,998 measurements and 62,883 observations,
  of 808,706 rows.
  `value` (the duration, or 1) is implied by the times and not kept; `location` (the line's
  site) has no field in procedure_occurrence and is not kept either.
- ICU dates (`icu.datetimeevents`) record that something happened on a date: a line was
  inserted, a dressing, cap or tubing changed, a pressure ulcer identified. Nurses chart the
  same date again every shift, and again in later ICU stays while the line stays in (9,979,761
  MIMIC-IV 3.1 rows for 1,590,212 distinct patient, item and date). Each (patient, item, date)
  is one record, dated at the date charted (`value`), not at `charttime`, in the ICU stay where
  it was first charted; the repeats are left out by design
  ([review R34](../review.md#r34-icu-dates-dated-when-they-happened)). A date cannot be later than
  its first recording (`storetime`), which corrects mistyped future dates (34,511 records). Items
  with a date but no time (`valueuom` 'Date') keep the date and no datetime: at midnight, a
  line inserted that afternoon would appear hours before it was, which a model predicting
  within the day sees (found in evaluation E3a; [results](../evaluation-results.md#e3a-same-labels-different-features)). CVB's `mimiciv_icu.datetimeevents.itemid`
  mapping (and its ICU item labels for one item it lacks) gives insertion procedures (peripheral
  IV, central line, arterial line, PICC, Foley, ICP and dialysis catheters, IABP) and, uphill,
  'Change of dressing'; caps, tubing and other dressing changes have only CVB's post-coordinated
  'Has asso proc' pairs ('Replacement of device' + 'Catheter') and stay at concept 0. In
  MIMIC-IV 3.1: 1,494,419 procedures (40.0% mapped); 'Impaired skin integrity' for the pressure
  ulcer and skin impairment dates goes to condition_occurrence (83,742), and the last dialysis,
  last menstrual period, referral and delivery dates to measurement and observation (12,051).
- Microbiology tests whose concept is a Procedure (KOH preparation, DNA probe analysis; 16 demo
  rows) are procedures, linked to their specimen ([specimen](specimen.md)). The organism a test
  found has no field here: "ISOLATE FOR MIC", which CVB maps only to the broader Isolation
  technique, loses the organism for 911 MIMIC-IV 3.1 rows; their susceptibilities link to this
  procedure record and so lack it too ([review R31](../review.md#r31-microbiology-without-a-settled-convention)).

## Fields (for `procedures_icd` and `hcpcsevents`)

| Field | Source / logic |
|---|---|
| `procedure_occurrence_id` | `prefix * 10^15 + key * 10 + k`: prefix 2 for `procedures_icd`, key `(hadm_id * 1000 + seq_num) * 10 + icd_version - 9` (2 admissions are coded in both ICD-9 and ICD-10 with the same `seq_num`); prefix 3 for `hcpcsevents`, key `hadm_id * 1000 + seq_num` |
| `person_id` | `subject_id` |
| `procedure_concept_id` | standard concept of the code, else 0 |
| `procedure_date` | `chartdate` |
| `procedure_datetime` | not populated (no time in the source) |
| `procedure_end_date` | not populated |
| `procedure_end_datetime` | not populated |
| `procedure_type_concept_id` | 32821 EHR billing record |
| `modifier_concept_id` | not populated |
| `quantity` | not populated |
| `provider_id` | not populated |
| `visit_occurrence_id` | `hadm_id` |
| `visit_detail_id` | not populated (no time within the admission) |
| `procedure_source_value` | `icd_code` or `hcpcs_cd` |
| `procedure_source_concept_id` | Athena concept of the code, else CVB's MIMIC4 concept, else 0 |
| `modifier_source_value` | not populated |

### ICU procedures (`procedureevents`)

The fields that differ from the table above:

| Field | ICU procedure |
|---|---|
| `procedure_occurrence_id` | `13 * 10^15 + (stay_id * 10^5 + seq) * 10 + k`, seq the row's position in its ICU stay by `starttime` |
| `procedure_concept_id` | CVB target of `itemid`, else 0 |
| `procedure_date` | date of `starttime` |
| `procedure_datetime` | `starttime` |
| `procedure_end_date` | date of `endtime` |
| `procedure_end_datetime` | `endtime` (never before `starttime`) |
| `procedure_type_concept_id` | 32817 EHR |
| `visit_occurrence_id` | `hadm_id` |
| `visit_detail_id` | the care unit stay covering `starttime`, or the ICU stay's first or last within 6 hours before or 1 hour after it |
| `procedure_source_value` | `itemid` |
| `procedure_source_concept_id` | CVB's MIMIC4 concept of the item |

### ICU dates (`datetimeevents`)

| Field | ICU date |
|---|---|
| `procedure_occurrence_id` | `19 * 10^15 + (stay_id * 10^6 + seq) * 10 + k`, stay_id the ICU stay of the first charting, seq the record's position in that stay by `itemid` and date |
| `procedure_concept_id` | CVB target of `itemid` (or its broader concept), else 0 |
| `procedure_date` | date of `procedure_datetime` |
| `procedure_datetime` | `value`, or the first `storetime` of the date when that is earlier |
| `procedure_type_concept_id` | 32817 EHR |
| `visit_occurrence_id` | `hadm_id` of the first charting |
| `visit_detail_id` | the care unit stay covering `procedure_datetime`; for a date without time charted first on that day, the one covering that charting |
| `procedure_source_value` | `itemid` |
| `procedure_source_concept_id` | CVB's MIMIC4 concept of the item |

The same fields apply to the ICU dates routed to condition_occurrence, measurement and
observation; `value_source_value` (measurement, observation) keeps the date as charted.

