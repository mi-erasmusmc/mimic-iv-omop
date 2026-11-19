# ETL documentation

This folder documents how MIMIC-IV is converted to OMOP CDM 5.5: what each CDM table is built
from, field by field, which conventions are followed, and where the ETL had to make a choice. It
is written for review by someone who knows OMOP conventions and wants to check the ETL without
reading the code.

- [general.md](general.md): rules that apply to every table (identifiers, vocabularies and
  mapping policy, how coded records are routed to tables, dates, validation).
- [dqd.md](dqd.md): running Achilles and DQD, and what DQD reports on the demo.
- [review.md](review.md): **decisions and open questions for review**, each with the relevant
  convention, what the ETL does, the alternatives, and numbers from the MIMIC-IV demo.
- [evaluation.md](evaluation.md): plan for evaluating the CDM against the source (conservation,
  phenotypes, prediction, observation period strategies).
- [evaluation-results.md](evaluation-results.md): its results so far.
- `etl/<table>.md`: one page per CDM table, with a field-level mapping of every CDM field.

| Table | Built from |
|---|---|
| [person](etl/person.md) | `hosp.patients`, `hosp.admissions` |
| [death](etl/death.md) | `hosp.admissions.deathtime`, `hosp.patients.dod` |
| [care_site](etl/care_site.md) | `hosp.transfers.careunit` |
| [visit_occurrence](etl/visit_occurrence.md) | `hosp.admissions`, `hosp.transfers`, `ed.edstays`, `hosp.omr`, `hosp.labevents` |
| [visit_detail](etl/visit_detail.md) | `hosp.transfers` |
| [observation_period](etl/observation_period.md) | all clinical event tables, `hosp.patients` |
| [condition_occurrence](etl/condition_occurrence.md) | `hosp.diagnoses_icd`, `ed.diagnosis` (and other coded sources by domain) |
| [procedure_occurrence](etl/procedure_occurrence.md) | `hosp.procedures_icd`, `hosp.hcpcsevents`, `icu.procedureevents`, `icu.datetimeevents` (and others by domain) |
| [observation](etl/observation.md) | `icu.chartevents` assessments, `hosp.drgcodes`, and codes of other sources in the Observation domain |
| [measurement](etl/measurement.md) | `hosp.labevents`, `hosp.microbiologyevents`, `icu.chartevents`, `icu.outputevents`, `hosp.omr`, `ed.triage`, `ed.vitalsign` (and codes of other sources in the Measurement domain) |
| [drug_exposure](etl/drug_exposure.md) | `hosp.prescriptions`, `hosp.emar`, `icu.inputevents`, `ed.medrecon`, `ed.pyxis` |
| [device_exposure](etl/device_exposure.md) | records of the drug sources whose concept is a Device |
| [specimen](etl/specimen.md), [fact_relationship](etl/fact_relationship.md) | `hosp.microbiologyevents` (specimens, linked to their organisms and susceptibilities) |
| [episode](etl/episode.md), [episode_event](etl/episode_event.md) | coded records whose concept is in the Episode domain |
| [payer_plan_period](etl/payer_plan_period.md) | `hosp.admissions.insurance` |
| [condition_era](etl/condition_era.md), [drug_era](etl/drug_era.md) | condition_occurrence, drug_exposure |
| [cdm_source](etl/cdm_source.md), [metadata](etl/metadata.md) | run options, versions |
| [vocabulary tables](etl/vocabulary.md) | Athena, plus CVB's MIMIC4 vocabulary |

## Conventions checked against

1. The [OMOP CDM 5.5 specification](https://ohdsi.github.io/CommonDataModel/cdm55.html): its
   table and field descriptions, user guidance and ETL conventions. The ETL's schema and
   validation are generated from the specification's field-level CSV.
2. The [THEMIS conventions](https://ohdsi.github.io/Themis/), the OHDSI community's ETL
   conventions beyond the specification.
3. Where neither says anything, the choices of the existing
   [OHDSI/MIMIC](https://github.com/OHDSI/MIMIC) ETL were considered, and departures from it
   are noted.

Each convention on the table pages is marked as one of:

- **CDM** or **THEMIS**: required or recommended there, and followed (with a link).
- **Decision**: neither source settles it; the ETL's choice and its reason are given, and the
  decision is listed in [review.md](review.md).
- **Deviation**: the ETL departs from a convention, with the reason; also listed in review.md.

## Keeping this accurate

The code is the source of truth, and each module's docstring states its conventions briefly.
These pages explain them in full. A test (`tests/test_docs.py`) fails if a CDM field of a built
table is missing from its page (the vocabulary tables, mostly copies of Athena, share one page),
so a new field cannot go undocumented. Numbers quoted here come
from the MIMIC-IV demo (100 patients) with the Athena release of 29-AUG-26 and CVB at commit
`bf5701cf`; they illustrate, and will differ on the full data.
