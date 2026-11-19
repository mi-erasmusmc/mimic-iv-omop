# observation_period

**Source:** the dates of all clinical event records (visit_occurrence, condition_occurrence,
procedure_occurrence, measurement, observation, drug_exposure, device_exposure), year of birth
and death date.
**Code:** `src/mimic_omop/etl/observation_period.py`.

## Conventions

The THEMIS convention for EHR data
([Observation Periods for EHR data](https://ohdsi.github.io/Themis/obs_periods_for_ehr.html)) is
followed:

- **THEMIS:** a period starts at the first clinical event, and not before birth (January 1 of
  the year of birth, the only birth information MIMIC has).
- **THEMIS:** a gap of 548 days or more between clinical events ends a period; the next starts at
  the next event. Both start and end dates of records count as event dates.
- **THEMIS:** a period ends 60 days after its last clinical event, or 60 days after death if that
  is earlier. The third THEMIS bound, the data extraction date, is not known per patient.
- **THEMIS:** death alone creates no period.
- **Decision:** a bound that would end a period before it starts is not applied, so every person
  with clinical events keeps a period and their records: a recorded death before the period's
  first event, or a year of birth after its events. See
  [review R28](../review.md#r28-death-recorded-before-a-persons-care).
- **CDM:** periods of a person never overlap or touch ("Any two overlapping or adjacent
  OBSERVATION_PERIOD records have to be merged into one"), and every person has at least one;
  persons without clinical events are excluded (see [person](person.md)).
- **THEMIS** (ratified): events may fall outside observation periods, but cannot be a cohort's
  index event ([events outside the observation period](https://ohdsi.github.io/Themis/events_outside_obs_period.html)).
- `period_type_concept_id` is 32882, Standard algorithm from EHR.

Whether THEMIS's 548-day window suits an inpatient-only source is the main open question. See
[review R1](../review.md#r1-observation-periods-themis-ehr-convention-vs-encounter-based) for the
trade-off and the demo numbers.

## Encounter-based alternative

`mimic-omop run --observation-periods encounter` builds encounter-based periods instead, for the
[evaluation](../evaluation.md#observation-periods) of both strategies. The strategy used is
recorded in [metadata](metadata.md) (`observation_period_strategy`).

- **Decision:** a period covers the days with recorded care: a gap of 2 days or more between
  clinical events ends it (adjacent days are merged, as the CDM requires), and it ends at its
  last event, with no padding.
- **Decision:** death does not bound an encounter-based period. Without padding, the death bound
  would cut off care recorded after the death date (4 demo admissions end with a transfer the
  day after death), so that the period would no longer cover every recorded event.
- Birth, the start at the first event, and death alone creating no period are as above.

## Fields

| Field | Source / logic |
|---|---|
| `observation_period_id` | `person_id * 1000 +` the period's number for that person, from 0 |
| `person_id` | `person_id` of the events |
| `observation_period_start_date` | first event date of the period, not before January 1 of the year of birth (both strategies) |
| `observation_period_end_date` | last event date of the period + 60 days, or death + 60 days if earlier; encounter-based: the last event date |
| `period_type_concept_id` | 32882 Standard algorithm from EHR |
