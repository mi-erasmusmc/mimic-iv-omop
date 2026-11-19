# Decisions and open questions for review

Each item gives the relevant convention, what the ETL does now, the alternatives, and numbers
from the MIMIC-IV demo (100 patients). **Status** is one of *follows convention* (listed because
the choice of convention matters), *decision* (no convention settles it), *deviation* (departs
from a convention) or *open* (known gap, not yet done).

Items most worth an expert's time come first.

---

## R1. Observation periods: THEMIS EHR convention vs encounter-based

**Status:** decided (2026-10-28): follow THEMIS. The [evaluation](evaluation.md#observation-periods)
compares both strategies, and this may be revisited with its results; `run --observation-periods
encounter` builds the alternative.

**Convention.** CDM: periods are spans with "a high capture rate of Clinical Events", must not
overlap or be adjacent, and every person needs one; how to infer them from EHR data is left to
the ETL. THEMIS [Observation Periods for EHR data](https://ohdsi.github.io/Themis/obs_periods_for_ehr.html):
start at the first clinical event (not before birth); a gap of 548 days or more starts a new
period; end at the earliest of last clinical event + 60 days, death + 60 days, or the data
extraction date; death alone creates no period.

**ETL.** Implements THEMIS (`etl/observation_period.py`, `PERSISTENCE_WINDOW = 548 days`,
`END_PADDING = 60 days`). The data extraction date is not known per patient in MIMIC (dates are
shifted per patient), so that bound is not applied.

**Why this needs review.** MIMIC-IV is one hospital's inpatient and ED data plus some outpatient
labs. Between admissions, care elsewhere is not recorded, so "high capture rate" is doubtful for
the time THEMIS counts as observed. The alternative is encounter-based periods: only days with
recorded care, adjacent days merged, no padding and no death bound
([details](etl/observation_period.md#encounter-based-alternative)).

| | Demo, THEMIS (current) | Demo, encounter | MIMIC-IV 3.1, THEMIS | MIMIC-IV 3.1, encounter |
|---|---:|---:|---:|---:|
| persons | 100 | 100 | 364,627 | 364,627 |
| observation periods | 113 | 1,235 | 425,842 | 4,377,805 |
| median length (days) | 200 | 0 | 111 | 0 |
| observed person-days | 56,684 | 3,352 | 221,983,406 | 7,603,332 |
| deaths inside an observation period | 27 of 31 | 15 of 31 | 27,708 of 38,301 | 12,947 of 38,301 |
| visits / conditions / measurements / drugs outside every period | 0 | 0 | 82 / 77 / 5,072 / 754 | 0 / 0 / 0 / 1 |

Figures of the builds of 2026-10-30 (with MIMIC-IV-ED, microbiology, ICU procedures and ICU
dates). On the full data, the records outside THEMIS periods are all but one dated more than 60
days (65 to 2,141 days) after the recorded death, which THEMIS's death bound excludes by design. The
exception, outside in both builds, is one prescription dated 1931, two centuries before the rest
of its patient's shifted dates (a source error).

THEMIS makes incidence denominators 17 times larger (29 times on the full data) and keeps most post-discharge deaths inside
observation (useful for mortality outcomes); encounter-based periods make absence of a record in
an observation period meaningful, at the cost of very short periods. Events outside observation
periods are allowed either way, but cannot be a cohort's index event (**THEMIS**, ratified,
[events outside the observation period](https://ohdsi.github.io/Themis/events_outside_obs_period.html)).

## R2. Visit detail concepts are not always descendants of the visit concept

**Status:** deviation (forced by the vocabulary), reported on every run.

**Convention.** CDM user guidance for VISIT_DETAIL: "the VISIT_DETAIL_CONCEPT_ID will be a
descendant of the VISIT_CONCEPT_ID for the Visit".

**ETL.** Visit details are care-unit stays with concepts ER (9203), Intensive Care (32037),
Observation Room (581385) or Inpatient (9201). In the vocabulary, Emergency Room and Inpatient
Visit (262) has **no descendants**, and Observation Room sits under Outpatient Visit (9202), so
any ED, ward or ICU detail of a 262 or Observation Room visit breaks the rule.

| Demo visit concept → detail concept | rows |
|---|---:|
| 262 → 9201 Inpatient | 200 |
| 262 → 9203 ER | 113 |
| 581385 → 9201 Inpatient | 102 |
| 581385 → 9203 ER | 95 |
| 262 → 32037 Intensive Care | 84 |
| 581385 → 32037 Intensive Care | 23 |
| 9201 → 581385 Observation Room | 1 |
| consistent (9201 → 9201/32037, 9203 → 9203, 581385 → 581385) | 297 |

**Alternatives.** (a) Keep the informative detail concepts (current). (b) Give details the
visit's own concept and keep the unit only in `visit_detail_source_value` and `care_site_id`;
consistent, but loses ICU and ED as concepts. (c) Raise with the vocabulary team whether ER (9203)
and Inpatient (9201) should be descendants of 262.

## R3. Observation admissions with ICU stays

**Status:** decision (revised 2026-10-30, after E2b).

**ETL.** `EU OBSERVATION` and `AMBULATORY OBSERVATION` admissions become Observation Room
(581385), an outpatient concept; `OBSERVATION ADMIT` and `DIRECT OBSERVATION` admissions are
inpatient stays like the others (Inpatient, or Emergency Room and Inpatient when they began in
the ED).

**Why.** In MIMIC-IV 3.1, `OBSERVATION ADMIT` admissions (84,437) last a median 4.6 days, as long
as urgent (4.0) and emergency (3.8) admissions, and in the demo 23 of 45 include an ICU stay: they
are inpatient care in all but billing status. `EU OBSERVATION` (119,456; median 0.7 days) and
`AMBULATORY OBSERVATION` (7,195; 0.9 days) are observation stays. Typing all four as Observation
Room, as the ETL first did, kept 23% of the patients of an "AMI with inpatient admission" cohort
out of it, since Observation Room sits under Outpatient Visit
([E2b](evaluation-results.md#e2b-ohdsi-cohorts-on-the-cdm-and-on-the-source)). Alternative: decide
per admission, Observation Room only for admissions without a ward or ICU transfer.

## R4. Laboratory Visits for labs without an admission

**Status:** decision.

**ETL.** 26% of demo labs have no `hadm_id`. Those inside an admission, ED stay or outpatient
measurement day (see [R23](#r23-outpatient-visits-for-omr-days)) by time are assigned to it; the
rest get one Laboratory Visit (32036) per person and day, spanning that day's labs (193 such
visits in the demo). The CDM describes 32036 as a visit "for the purpose of a Measurement". Alternatives: Outpatient Visit (9202), on the view that these
labs come from clinic visits MIMIC does not otherwise record; or no visit at all (allowed, as
visit_occurrence_id is optional), which would also keep them out of observation periods under
the encounter-based alternative of R1.

## R5. Diagnoses are dated at the start of their visit

**Status:** follows the common OHDSI practice; consequence worth knowing.

`diagnoses_icd` has no dates: codes are assigned for billing at discharge. They are dated at the
start of their visit. For prediction or any design with an index date during an admission, that
admission's diagnoses must not be used as predictors, since they are only known at discharge.
Alternative: date them at visit end, which is honest about when they are known but places
conditions after the events they explain.

## R6. Condition status from the diagnosis sequence number

**Status:** decision.

`seq_num = 1` becomes Primary diagnosis (32902), others Secondary diagnosis (32908). The old
OHDSI ETL used the 'Inpatient detail' concepts per position instead. MIMIC documents `seq_num`
as priority, not as admission or discharge diagnosis, so the discharge-specific concepts are not
used.

## R7. Race and ethnicity from a combined source string

**Status:** decision; to raise with CVB.

MIMIC has one `race` string per admission (e.g. `HISPANIC/LATINO - PUERTO RICAN`,
`BLACK/CAPE VERDEAN`). The person's string is from the latest admission with an informative value.
CVB maps some strings to detailed Ethnicity-domain concepts; those cannot be race concepts, so
race falls back to the string's prefix (WHITE, BLACK, ASIAN) where there is one, else 0.
`ethnicity_concept_id` is the OMB category: Hispanic for `HISPANIC*` and `SOUTH AMERICAN`, 0 for
unknown, declined or multiple, Not Hispanic otherwise.

## R8. Type of deaths known only from `dod`

**Status:** decision.

`patients.dod` adds deaths after discharge from state records (16 of 31 deaths in the demo).
They are typed Registry (32879); in-hospital deaths from `deathtime` are EHR (32817). Death
Certificate (32815) would also be defensible.

## R9. CVB and Athena disagree on some codes

**Status:** decision.

For ICD-9/10 diagnosis and procedure codes and CPT4/HCPCS, Athena's mapping is used and CVB only
fills codes Athena does not map. With the 29-AUG-26 release, CVB and Athena map 40 codes
differently (33 diagnoses, 7 HCPCS). Worth reviewing whether CVB's choices are corrections that
should go to Athena.

## R10. CVB custom concepts are not in the output vocabulary

**Status:** resolved.

CVB's MIMIC4 source concepts (ids from 2,000,000,000), written as `*_source_concept_id` for lab,
ICU and drug items, DRGs and race, are part of the output vocabulary tables, with their
vocabulary, concept classes and relationships (see [vocabulary](etl/vocabulary.md)). The build
checks that every concept id in the clinical tables resolves.

## R11. Lab text results are not mapped to value concepts

**Status:** open.

Results such as `NEG`, `POS` or `ART.` are kept in `value_source_value` but get no
`value_as_concept_id`; CVB has no mapping for lab result strings. 12% of demo labs have no
numeric value.

## R12. Targets outside the event domains

**Status:** decision.

A mapping to a concept outside the clinical event domains (e.g. lab item 51706 'Problem
Specimen' maps to the Meas Value concept 'Sample could not be processed') cannot be a record's
main concept. Such records get concept 0 in the source's natural table, keeping source value and
source concept. Alternative: treat the target as `value_as_concept_id` of a record with concept 0.

## R13. Visit boundaries

**Status:** decision.

- An admission's visit spans `admittime` to `dischtime` widened to cover all its transfers: in the
  demo, transfers start up to 13.5 hours before `admittime` and end up to 27 hours after
  `dischtime`. Visit details therefore always lie inside their visit.
- The ED part of an admission comes from its ED transfers, not `edregtime`: consecutive admissions
  can share one ED registration (in the demo an observation stay and the admission after it both
  list it), and the transfers table assigns the ED stay to the first. `edregtime` is used only for
  admissions without transfers.
- ED stays in `transfers` without an `hadm_id` become ER visits (9203); contiguous rows are one
  stay.
- One remaining demo overlap of 23 seconds, where one admission ends as the next begins, is left as
  it is; the CDM allows visits to share their start and end days.

## R14. Persons without clinical events are excluded

**Status:** follows convention.

The CDM requires an observation period for every person, and observation periods need clinical
events, so persons without any are excluded (**THEMIS**, ratified, [person
exclusion](https://ohdsi.github.io/Themis/excluded_persons.html), which allows documented
exclusions). None in the demo. As THEMIS asks, the number excluded and the reason are recorded
in the [metadata](etl/metadata.md) table.

## R15. Year of birth

**Status:** decision.

`year_of_birth = anchor_year - anchor_age`, so it can be off by one year, and for patients over
89 (reported as 91; 3 in the demo) it is not their real year of birth. Month and day of birth are
not available.

## R16. Care sites

**Status:** decision.

One care site per care unit name in `transfers` (31 in the demo). `place_of_service_concept_id`
comes from CVB's care unit mapping, which covers only 4 of them.

## R17. Orders and administrations are both recorded

**Status:** decision.

drug_exposure holds orders (`prescriptions`, EHR order 32833) and administrations (`emar` and
`inputevents`, EHR administration record 32818), so a drug that was ordered and given appears
under both types. Drug eras merge overlapping exposures of the same ingredient, so this does not
inflate eras, but counts of exposures and cohort definitions should choose a type. eMAR does not
cover every admission: in the demo, 55 of 135 admissions with orders have eMAR records among
patients whose anchor year group is 2011–2013, and 105 of 115 for 2014–2016, so for many earlier
admissions orders are the only drug record. Alternatives: administrations only, with orders kept for drugs or periods without
eMAR; or orders only, as in the old OHDSI ETL.

## R18. Which eMAR actions are administrations

**Status:** decision.

THEMIS (ratified): records only for actions meaning the drug was administered, none for missed
or refused doses. eMAR has 44 actions in the demo. After removing a `Delayed ` prefix and an
` in Other Location` suffix, these count as given: Administered, Administered Bolus from IV Drip,
Partial Administered, Confirmed, Started, Restarted, Rate Change, Applied, Removed Existing /
Applied New, Flushed. Everything else is left out, including Not Given, Hold Dose, Assessed,
Stopped, Removed and Infusion Reconciliation, and any action not in the list (new actions are
excluded until reviewed). The full MIMIC-IV 3.1 has 72 actions (40 after removing the prefix and
suffix); the ones the demo lacks are left out too: Not Started, Not Confirmed, Stopped As
Directed, Pain score re-assessment, Documented in O.R. Holding (2,433 rows), TPN Rate Not
Changed, Removed - Unscheduled, Not Read, Partial (4 rows) and their sliding-scale variants.
**Decision (2026-10-30):** `Confirmed` (a nurse confirming that an infusion is still running,
1.9M rows in 3.1) is not an administration: it re-documents one already recorded, and counting it
would inflate counts of exposures. `Flushed` (line flushes, mostly saline or heparin, 3.4M) is a
drug given and counts. `Documented in O.R. Holding` and `Partial` stay out.

## R19. ICU input items without a mapping, and route

**Status:** open.

CVB gives 180 of the 283 `inputevents` items a 'Maps to'; for most of the rest it records only
hierarchy relationships ('Is a', 'Subsumes'). Items with an `Is a` are now mapped to that broader
concept (R29); those with only a `Subsumes` or no relation keep concept 0. ICU inputs get no route: `inputevents` has no route
field, though `ordercategorydescription` (continuous IV, drug push, ...) implies one.

## R20. Dose and quantity are not populated

**Status:** decision for eMAR (2026-10-30); open for orders and ICU inputs.

`quantity` is the number of units of the drug product, which MIMIC does not give directly:
prescriptions carry the prescribed dose (`dose_val_rx`, `dose_unit_rx`) and the dispensed form
amount, eMAR details the dose given, ICU inputs an amount and rate in their own units.

- **Decision (eMAR):** `hosp.emar_detail` records, per administration, each product given with
  its amount and unit (`product_amount_given` 1, `product_unit` TAB). An administration of one
  product gets that amount as `quantity` and the unit in `dose_unit_source_value`; the dose is
  quantity times the concept's strength (DRUG_STRENGTH), as the CDM intends. Administrations of
  several products (levothyroxine 112 mcg and 25 mcg tablets for one dose) get none: summing
  their tablets would not be a quantity of the record's one concept. The dose MIMIC records
  (`dose_given`, `dose_given_unit`) has no CDM field.
- **Open:** orders. The OHDSI ETL writes the dispensed form amount (`form_val_disp`) as quantity,
  but that is the amount per dose, while THEMIS reads quantity of a prescription as the total
  dispensed. ICU inputs give amounts in mL or the drug's own unit (`amount`, `amountuom`), which
  could be quantity for solutions.

## R21. Local mapping for omr

**Status:** decision; to propose to CVB.

CVB does not map `hosp.omr`, MIMIC's outpatient record of weight, height, BMI and blood pressure,
so the ETL has one local mapping: `src/mimic_omop/mappings/omr.csv`. It is written as LOINC and
UCUM codes, resolved through Athena on every run, and a test checks that every code resolves to
a valid standard concept.

| result_name | LOINC | UCUM |
|---|---|---|
| Weight (Lbs), Weight | 29463-7 Body weight | `[lb_us]` pound |
| BMI (kg/m2), BMI | 39156-5 Body mass index | `kg/m2` |
| Height (Inches), Height | 8302-2 Body height | `[in_i]` inch |
| Blood Pressure, and its Sitting / Lying / Standing variants | 8480-6 Systolic, 8462-4 Diastolic | `mm[Hg]` |

**Decision:** the result names without a unit, `Weight` (15,108 rows in MIMIC-IV 3.1), `Height`
(2,365) and `BMI` (29,021), are mapped like their labelled counterparts, in pounds, inches and
kg/m2. The unit is inferred, not recorded; the evidence that it is the same:
- the distributions match: `Weight` 1st/50th/99th percentile 95/170/333 (`Weight (Lbs)`
  96/170.5/321), `Height` median 65.5 (`Height (Inches)` 65.25), `BMI` 28.0 (28.0);
- within the same patients they agree: the median ratio of a patient's `Weight` to their
  `Weight (Lbs)` is 0.98 (6,956 patients with both; 90% between 0.80 and 1.16), of `Height` to
  `Height (Inches)` 1.00 (1,221 patients);
- there is no cluster of kilogram-sized weights (27 `Weight` values under 70, a share similar
  to `Weight (Lbs)`), and the two names never occur on the same day, which suggests an older
  label for the same field. One `Height` of 152.4 is centimetres and is written as inches, as
  in the source.

`eGFR` (279 rows) stays unmapped: the equation, and so the LOINC code, is not recorded.
Blood pressure
positions map to the generic concepts; LOINC has position-specific ones (e.g. 8459-0 for sitting
systolic).

## R22. Unmapped chart items

**Status:** decision.

CVB maps chart items that make up 42% of demo `chartevents` rows; the rest are mostly nursing
documentation (safety measures, positioning, lung sounds, care plans). An unmapped item goes to
measurement when its value is numeric and to observation when it is text only, with concept 0
and the text in `value_as_string`. In the demo, 16% of the 367,245 chart rows written to
observation have a concept. Alternative: leave unmapped nursing documentation out of the CDM.

## R23. Outpatient Visits for omr days

**Status:** decision.

`omr` rows are outpatient measurements dated by day. A day not inside an admission or ED stay
gets an Outpatient Visit (9202) for the whole day (00:00 to 23:59:59), and labs that day fall in
it rather than in a Laboratory Visit (829 Outpatient Visits in the demo). The visit's times are
the day's bounds, not real times, because `omr` records none.

## R24. CVB's standard MIMIC4 concepts

**Status:** decision.

CVB marks 2,249 of its MIMIC4 concepts standard: items it found no Athena standard concept for,
such as 454 ICU observation items, 426 ICU measurement items and 350 drug names. Where CVB
relates such an item to a broader Athena concept, the record gets that concept (R29), and the
MIMIC4 concept is written as non-standard with a 'Maps to' to it. Records of the other items get
concept 0, keeping the MIMIC4 concept as `*_source_concept_id`; those MIMIC4 concepts are in the
output vocabulary with a 'Maps to' to themselves. Using them as the
records' concepts instead would make more of MIMIC usable within MIMIC, but such concepts mean
nothing in other databases, so network studies would not see the records either way.

## R25. Discharge to home is concept 0

**Status:** follows convention; differs from CVB.

The CDM: "It is assumed that a person is discharged to home therefore there is not a standard
concept id for 'home'. Use concept id = 0 when a person is discharged to home"; likewise
`admitted_from_concept_id` is 0 for admission from home or self-referral. CVB maps the discharge
location `HOME` to Home Visit (581476), which is a provider visiting the patient, so the ETL sets
`HOME` to 0 and keeps CVB's mapping for `HOME HEALTH CARE`. DQD's
`standardConceptRecordCompleteness` counts these zeros as unmapped (see [dqd.md](dqd.md)).

## R26. Feedback for CVB

**Status:** open; to send to the CVB maintainers later (2026-10-30).

Found while building and checking the CDM:

- Discharge location `HOME` mapped to Home Visit, where the CDM asks for 0 (R25).
- Lab item 52172 (RDW-SD, reported in fL) mapped to LOINC 788-0, an RDW concept reported in %.
- MIMIC4 concepts use the domains 'Undefined' (74 concepts) and 'Waveform Metadata' (44), which
  are not defined in Athena or in CVB's `domain_delta` (see [vocabulary](etl/vocabulary.md)).
- Race strings mapped to detailed Ethnicity-domain concepts, which cannot be race concepts (R7).
- `hosp.omr` is not mapped; the ETL's local mapping (R21) could be contributed.
- 40 ICD and HCPCS codes are mapped differently from Athena (R9).
- Items mapped only by `Is a` / `Subsumes` (R29): whether ETLs should use CVB's standard MIMIC4
  concepts, and with which ancestors, or map uphill as this ETL does. Items with several
  unrelated `Is a` targets (e.g. "Motor L Arm" `Is a` both "Physical findings of Motor function"
  and "Physical findings of Upper arm") cannot be mapped uphill without choosing one.
- Lab serum indices H (50934, not listed), L (51678) and I (50947), labelled junk: 1.7% of
  MIMIC-IV 3.1 lab rows each, and LOINC has interference index concepts for them (e.g. 46424-8
  hemolysis, 46426-3 icteric).
- ICU fingerstick glucose (225664, 1.0 M rows; MIMIC gives no unit, median 140, so mg/dL) is
  mapped to LOINC 14743-9, glucose in moles per volume in capillary blood; a mass-per-volume code
  (e.g. 41653-7, glucose in capillary blood by glucometer) would match the values (found by
  [evaluation E2a](evaluation-results.md#e2a-mimic-code-concepts-on-the-source-and-on-the-cdm)).
- Lab 51081, "Creatinine, Serum" with fluid Urine, is mapped to serum creatinine (2160-0).
- 11 microbiology codes are written with a field prefix (`microbiologyevents.test_itemid||90093`,
  `microbiologyevents.interpretation||I`), one test with the wrong field
  (`microbiologyevents.spec_itemid||90189` in the test_itemid set); the ETL strips the prefix.
- Test "ISOLATE FOR MIC" is related only to the procedure "Isolation technique" (R31).
- MIMIC-IV-ED is not mapped (R32, R33): its vital signs, triage and Pyxis GSN codes need local or
  derived mappings.
- ICU item 229661 'Compliance' (lung compliance) is related `Is a` 'Compliance behavior to
  treatment' (606892, adherence); through the uphill rule (R29) its 185,694 values become
  observations of adherence. Candidates are LOINC 60826-5 'Lung compliance' (21490581) or its
  static and dynamic variants (60828-1, 60827-3), measured in mL/cm[H2O] (720866); MIMIC charts it
  in cmH2O/L/seconds, a unit of resistance (found by DQD, [dqd](dqd.md)).
- Further wrong `Is a` relationships found reviewing the uphill mappings (R29,
  [uphill-review](uphill-review.md)): 'Goal Richmond-RAS Scale' (228299) `Is a` RASS; 'Multi
  Lumen', 'IABP' and 'Presep Catheter Placement Confirmed by X-ray' (227756, 227754, 227760) `Is
  a` the insertion procedures; lab items 'Blue / Light Green / Red Top Hold' (50887, 50955, 50979)
  `Is a` Blood chemistry and 'Anat Path Hold' (51839) `Is a` Body fluid analysis. The ETL
  excludes them, and the Compliance one above, locally (`mappings/cvb_exclusions.csv`).
- Microbiology specimen types without a mapping: MRSA SCREEN (73,972 specimens), ANORECTAL/VAGINAL
  (12,677), Rapid Respiratory Viral Screen & Culture (10,064), Influenza A/B by DFA (3,827).
- ICU date items for cap, tubing and dressing changes have only post-coordinated `Has asso proc`
  pairs, no equivalent (R34; 9.6% of procedure_occurrence at concept 0).
- Insurance is mapped to LOINC answers; payer_plan_period needs SOPT payers (the ETL's local
  mapping, [payer_plan_period](etl/payer_plan_period.md)).

## R27. Discharge medications

**Status:** decision (confirmed 2026-10-30); found in the full data (the demo has none).

MIMIC-IV 3.1 has 21,731 prescriptions whose pharmacy record is a discharge medication; all lack a
start time and 97% a stop time. They are written as EHR prescription (32838), starting at the
pharmacy entry time and ending at the stop time or else 29 days later (THEMIS
[end date](https://ohdsi.github.io/Themis/drug_end_date_not_in_data.html), written prescription).
They extend drug eras and observation periods past discharge. Alternatives: leave them out, as
MIMIC does not record whether they were filled; or keep them only as orders at discharge.

## R28. Death recorded before a person's care

**Status:** decision; found in the full data.

In MIMIC-IV 3.1, 19 persons have a `dod` months to years before every visit they have. The
THEMIS end bound (death + 60 days) would end their observation period before it starts, which
would drop the period and so the person, while their clinical records remain. The ETL keeps
them: the death bound is not applied to a period it would invert, and the death record is kept
as recorded (DQD's plausibleBeforeDeath reports such events after death). Alternative: exclude
these persons and all their records, recording the reason in METADATA.


## R29. CVB items mapped only to a broader concept

**Status:** decision (2026-10-28); found by the conservation check ([evaluation E1](evaluation.md#e1-conservation)).

**Convention.** OHDSI's 'Maps to' links a source code to the standard concept with the same
meaning; when there is none, the usual practice is to map to the closest broader concept
("uphill" mapping), never to a narrower one, since a narrower concept asserts more than the
source says.

**CVB.** For items without an equivalent standard concept, CVB records the item's relation to
standard concepts as `Is a` (`skos:broadMatch`: the item is narrower than the target, e.g. ICU
input "Midazolam (Versed)" `Is a` the RxNorm ingredient midazolam), `Subsumes`
(`skos:narrowMatch`: the item is broader, e.g. "Furosemide (Lasix)" subsumes "Furosemide
Injection [Lasix]") or a related-match relationship. In its own vocabulary release such items
become standard MIMIC4 concepts that 'Maps to' themselves, placed in the hierarchy by those
relationships (2,248 standard MIMIC4 concepts).

**Options considered.**
1. Concept 0 for these items (the ETL's first behaviour, using CVB's 'Maps to' rows only).
2. Uphill: use the `Is a` targets as the record's concept; leave `Subsumes` and related matches
   at 0.
3. CVB's design: write the standard MIMIC4 concepts as concept ids and add them, with ancestors
   derived from CVB's relationships, to the output `concept_ancestor`, so that descendant
   queries on, say, midazolam find them. Most faithful to CVB, but cohort definitions then
   depend on locally computed ancestors, and the concepts mean nothing in other databases.

**Decision:** option 2 (`vocab.resolve_cvb`; [general](general.md#vocabularies-and-mapping),
rule 3). A CVB code whose 'Maps to' gives no valid standard target is mapped to its `Is a`
targets, resolved through Athena like any CVB target. Where CVB names several broader concepts:
- one an ancestor of another (in Athena's `concept_ancestor`): the most specific is used, e.g.
  "Orthostatic BPs sitting" `Is a` both "Orthostatic blood pressure panel" and "Systolic blood
  pressure--sitting", and gets the latter;
- all drug ingredients (a combination product not in RxNorm, e.g. "HYDROmorphone/Bupivacaine/
  CloNIDine Intrathecal"): one record per ingredient, as for a one-to-many 'Maps to', so drug
  eras count each ingredient;
- otherwise, the item stays at 0: each target is a different broader meaning of the same
  measurement (e.g. "Motor L Arm" `Is a` "Physical findings of Motor function" and "Physical
  findings of Upper arm"), one record per target would count one value twice, and choosing one
  would be a local mapping. These are listed for CVB (R26).

The MIMIC4 concept of a code mapped this way is written to the output vocabulary as
non-standard, with a 'Maps to' to the broader concept instead of to itself
([vocabulary](etl/vocabulary.md)). The CDM does not record that a mapping is uphill; the CVB
curation file (`predicate_id` `skos:broadMatch`) and this item are the record of it. An analyst
querying a broad concept such as midazolam now finds the ICU infusions; one needing the exact
item still has `*_source_value` and `*_source_concept_id`.

**Effect on MIMIC-IV 3.1** (rows with a standard concept, before and after; from the
conservation check):

| Source | Codes with `Is a` only | now mapped | Mapped rows before → after | still 0 (several unrelated targets) |
|---|---:|---:|---:|---:|
| chartevents | 387 | 339 | 41.7% → 55.6% | 3.3 M rows (0.8%) |
| inputevents | 44 | 43 | 70.2% → 86.0% | 18 rows |
| outputevents | 4 | 4 | 89.6% → 92.3% | – |
| labevents | 11 | 11 | 93.0% → 93.7% | – |
| prescriptions (drug name) | 348 | 348 | 94.4% → 94.6% | – |
| diagnoses_icd (codes Athena does not map) | 100 | 99 | 99.4% → 100.0% | 1 row |

Among the recovered items are ICU infusions such as midazolam, hydromorphone and prophylactic
heparin, which matter for sedation, analgesia and SOFA-type definitions, and the ICD-10-CM place
of occurrence codes (Y92), which become "Place of occurrence of injury" observations. Some
targets are coarse: lab hold tubes ("Red Top Hold") become "Blood chemistry" measurements
without a value, as CVB classifies them. Items with only a `Subsumes` or a related match
(chartevents 3.0% and 11.2% of rows, inputevents 3.5% and 8.7%) keep concept 0.

Open for CVB (R26): whether option 3 is what they intend for ETLs.

**Review (2026-10-30):** `docs/uphill-review.md` lists the codes mapped this way, most frequent
first (`scripts/uphill_review.py`): 1,058 codes and 1,181 targets before the exclusions below,
1,049 and 1,172 after. A first pass found 9 wrong relationships, now in
the exclusion list (`mappings/cvb_exclusions.csv`, general rule 9) and fed back to CVB (R26):
ICU 'Compliance' as treatment adherence (185,694 rows, found by DQD), 'Goal Richmond-RAS Scale'
as RASS (1,385,305), three 'Placement Confirmed by X-ray' items as line or balloon insertions
(511,665) and four specimen 'Hold' lab items as blood chemistry or body fluid analysis
(887,766). The rest wants a mapping expert's look.

## R30. Chart rows outside their ICU stay

**Status:** decision (2026-10-29); found by [evaluation E2a](evaluation-results.md#e2a-mimic-code-concepts-on-the-source-and-on-the-cdm).

**Convention.** CDM: `visit_detail_id` is the visit detail "during which the event occurred".
The CDM has no ICU stay identifier; this ETL's visit details are care unit stays (transfers).

**Source.** Every ICU row carries its ICU stay's `stay_id`, but MIMIC-IV 3.1 charts 0.6% of
`chartevents` rows outside that stay's in and out times, and the two sides differ:

| | Rows | Distance from the stay | Care unit stay covering the time |
|---|---:|---|---|
| before `intime` | 2.32 M | median 31 min; 63% within 1 h, 82% within 6 h | ED 47%, PACU 18%, discharge lounge, wards; 9% none |
| after `outtime` | 0.41 M | median 56 h; 4.5% within 1 h | |

Rows just before arrival are charting at handover, ahead of the transfer's timestamp; rows days
after discharge look like late or misfiled entries rather than ICU care. `outputevents` (16 k
rows outside) and `inputevents` (27 k) behave alike. (Care units: from a sample of 200,000 rows.)

**Options.**
1. The care unit stay covering the row's time (the ETL's first behaviour). Literal, but
   admission vital signs charted at handover are attached to the ED or PACU, and an analysis of
   an ICU stay's first hours misses them (E2a: first-day vital sign means differed in 1.2–3.4% of
   stays).
2. Always the ICU stay from `stay_id`: also attaches rows charted days after discharge, so a
   record would lie outside its visit detail's times.
3. `stay_id` within a tolerance, else by time.

**Decision:** option 3 (`etl/events.py`, `with_icu_stay_detail`) for chart, output and ICU input
rows. A row up to 6 hours before its ICU stay's `intime` gets the stay's first care unit stay; a
row up to 1 hour after `outtime` its last; every other row keeps the care unit stay covering its
time. Six hours is mimic-code's window for "first day" concepts (from 6 hours before ICU
admission). In MIMIC-IV 3.1 this moves 1.90 M chart, 10 k output and 20 k input rows to their
ICU stay; the 0.82 M chart rows further outside stay as they were. Such a record lies before (or
after) the times of its visit detail by at most those tolerances.

**Effect** (E2a, with chart records attributed to ICU stays through `visit_detail_id`): first-day
vital sign means identical in 97.9–99.9% of stays (heart rate 99.94%), against 96.6–98.8% with
the covering care unit stay alone. Chart measurements without a visit detail fell from 348,374 to
70,296, together with ending transfers that have no `outtime` at the next transfer
([visit_detail](etl/visit_detail.md)).


## R31. Microbiology without a settled convention

**Status:** decision (2026-10-29; the open points confirmed as they are, 2026-10-30).

**Convention.** OHDSI has no ratified convention for cultures and antibiotic susceptibility
testing; forum threads from CDM v4 to 2026 propose different models, and recent advice is to use
the CDM's own link fields (`measurement_event_id`, 5.4+) and fact_relationship for what they
cannot hold. CVB maps MIMIC's microbiology codes: specimen types to Specimen concepts, tests to
LOINC or SNOMED culture and assay concepts, organisms to SNOMED organisms, antibiotics to LOINC
susceptibility concepts, and S/I/R to LOINC answers.

**ETL** ([specimen](etl/specimen.md)):
- a specimen record per `micro_specimen_id`;
- a test result per test, with the organism as `value_as_concept_id` (an isolate known only
  from its susceptibility rows gets a derived record), linked to the specimen in
  fact_relationship ('Has specimen' / 'Specimen of');
- a susceptibility measurement per antibiotic, with S/I/R as value, the MIC as number and
  operator, and `measurement_event_id` pointing at its isolate's test result.

The organism is a value, not a record of its own, so organism concepts (Observation domain in
SNOMED) appear as `value_as_concept_id` of measurements; DQD does not check value domains.

**Open points.**
- The MIC has no unit in MIMIC; `unit_concept_id` stays empty (DQD's measurement unit checks
  count these).
- Tests whose concept is a Procedure have no value field: "ISOLATE FOR MIC" (917 rows), which
  CVB relates only to the broader "Isolation technique", loses its organism (911 rows), and its
  susceptibilities link to that procedure record. Alternative: write test results with an
  organism to measurement whatever the test concept's domain.
- `quantity` (growth: "1+", "SPARSE GROWTH") and `isolate_num` are not kept.
- Lab specimens (`labevents.specimen_id`) are not specimen records; only microbiology specimens
  are.


## R32. A GSN crosswalk derived from MIMIC's prescriptions

**Status:** decision (2026-10-29); for MIMIC-IV-ED.

**Problem.** MIMIC-IV-ED's Pyxis dispensing table (`ed.pyxis`, 1.19 M medications) identifies
drugs only by First Databank GSN (Generic Sequence Number) and a name. Athena has no GSN
vocabulary, CVB does not map MIMIC-IV-ED, and Pyxis names match CVB's prescription drug names
for only 18.8% of medications. `ed.medrecon` has NDCs as well, but 12% of them do not map.

**Decision:** a crosswalk derived from the data (`etl/ed.py`, `gsn_map`). `hosp.prescriptions`
records a GSN and an NDC together; each GSN maps to the RxNorm concept its prescriptions' NDCs map
to through Athena, raised to the Clinical Drug it descends from (a branded or quantified drug
becomes its Clinical Drug, the level a GSN describes: one formulation, of any brand or pack), and
the most frequent if several remain. Of 3,156 GSNs, 70% have one NDC target and the others differ
by brand or pack (digoxin 0.125 MG Oral Tablet, and the same [Lanoxin]). The crosswalk is built
from all prescriptions on every run, so it does not depend on person buckets. It covers 96.9% of
Pyxis medications; medrecon uses its NDC first and the crosswalk for the rest.

**Alternatives.** Leave Pyxis unmapped (concept 0, the name as source value); or map Pyxis names
to CVB's drug names, which covers few.

## R33. MIMIC-IV-ED

**Status:** decision (2026-10-29).

MIMIC-IV-ED 2.2 (2011-2019 ED stays) is a separate PhysioNet module; it is converted into the same
parquet directory (`ed/`) and the ETL uses it when it is there. It is consistent with MIMIC-IV
3.1: 425,011 of its 425,087 stays start at the intime of an ED transfer of `hosp.transfers`; the
other 76 are stays of patients who are not in MIMIC-IV 3.1, and are left out. ED records belong
to the visit of that transfer, an ED-only visit or the admission that followed.

- **Decision:** ED vital signs and triage use a local mapping, `mappings/ed.csv`, written as LOINC
  and UCUM codes like the omr mapping (R21): temperature 8310-5 in °F, heart rate 8867-4,
  respiratory rate 9279-1, SpO2 59408-5 (pulse oximetry), blood pressure 8480-6 / 8462-4, pain
  72514-3 (0-10 score), ESI acuity 75636-1 (an Observation concept), chief complaint 8661-1,
  heart rhythm 8884-9 (as text). Blood pressure uses the generic codes, as for omr; ICU
  non-invasive pressures carry CVB's 'by Noninvasive' concepts. To propose to CVB with omr (R26).
- **Decision:** medicine reconciliation (the patient's medications before the ED stay) is
  drug_exposure typed 'EHR medication list' (32830), dated when documented, not when taken; the
  alternative is observations of medication history.
- **Decision:** the arrival transport and disposition of an ED stay become `admitted_from` and
  `discharged_to` of an ED-only visit: ambulance and helicopter as the CMS places of service
  Ambulance - Land and - Air or Water; dispositions through CVB's discharge location mapping
  (TRANSFER as ACUTE HOSPITAL, 38004279; EXPIRED as DIED, which CVB leaves unmapped, so 0 as for
  admissions), HOME as 0 (R25). Only 2,329 dispositions get a concept; the death is in `death`. For ED stays that led to an
  admission, the admission's own `admission_location` stays.
- ED diagnoses are coded for the ED stay, separately from the admission's; both are kept. ED
  pain values that are not numbers ('unable', 'sleeping') keep their text without a number.

## R34. ICU dates dated when they happened

**Status:** decision (2026-10-30, confirmed).

`icu.datetimeevents` (9,979,761 rows) records dates: a line was inserted on this date, its
dressing, cap or tubing changed, a pressure ulcer identified, the last dialysis. Nurses chart the
same date every shift and again in later ICU stays, so the rows hold 1,590,212 distinct
(patient, item, date). The OHDSI MIMIC ETL writes every row as a procedure dated at `value`, one
insertion per charting.

- **Decision:** one record per (patient, item, date), in the ICU stay and admission where it was
  first charted; the 8,389,549 repeats are left out by design and counted by E1.
- **Decision:** a record is dated at the date charted (`value`), when the thing happened, not
  when it was documented. A date cannot be later than its first recording (`storetime`); 34,511
  records were dated later, mostly typos of a month or year, and are dated at the recording.
- **Consequence:** a line inserted before the admission (in the ED before midnight, or a PICC or
  port weeks earlier) is dated before its visit: 32,573 records (2.0%), 16,725 by more than a day
  and 4,778 by more than 30 days. Observation periods cover all events, so 5,351 of 425,842 THEMIS
  periods (1.5%) now start earlier, by a median of 2 days (90% within 31 days, at most 7.4 years
  for an evident typo); 11 periods merged with an earlier one. Encounter-based periods (R1) get a
  period for such a day (8 more on the demo).
- **Alternative:** date records that precede their admission at the admission start, keeping the
  date in `value_source_value`: periods would be unchanged, but the procedure date would be wrong
  for the 2%.
