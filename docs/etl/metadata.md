# metadata

**Source:** the vocabulary and CVB versions, the ETL version and settings, and counts from the
built CDM.
**Code:** `src/mimic_omop/etl/cdm_source.py`.

## Conventions

- **THEMIS** (ratified, [person exclusion](https://ohdsi.github.io/Themis/excluded_persons.html)):
  reasons for removing persons are recorded in METADATA. The count of persons without clinical
  events (hence without an observation period, see [person](person.md)) is recorded here.
- The versions needed to reproduce the CDM are recorded: Athena release, CVB commit, ETL version,
  MIMIC-IV version, and the observation period strategy.
- No concepts describe these entries, so `metadata_concept_id` and `metadata_type_concept_id`
  are 0.

| `name` | `value_as_string` / `value_as_number` |
|---|---|
| vocabulary_version | Athena release, e.g. `v5.0 29-AUG-26` |
| cvb_version | `TuftsCTSI/CVB@<commit>` |
| etl_version | as `cdm_source.cdm_etl_reference` |
| source_version | `MIMIC-IV <--source-version>` |
| observation_period_strategy | the persistence window and end padding used |
| persons_excluded | the number of `hosp.patients` rows without a person record |

## Fields

| Field | Source / logic |
|---|---|
| `metadata_id` | 1, 2, ... in the order above |
| `metadata_concept_id` | 0 |
| `metadata_type_concept_id` | 0 |
| `name` | see above |
| `value_as_string` | see above |
| `value_as_concept_id` | not populated |
| `value_as_number` | the count, for persons_excluded |
| `metadata_date` | the CDM release date |
| `metadata_datetime` | not populated |
