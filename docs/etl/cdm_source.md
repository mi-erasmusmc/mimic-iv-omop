# cdm_source

**Source:** options of `mimic-omop run` (`--cdm-holder`, `--source-version`,
`--source-release-date`), the Athena vocabulary release, and the ETL's own version.
**Code:** `src/mimic_omop/etl/cdm_source.py`. One row.

## Conventions

- **CDM 5.5:** the table holds one record describing the database, which tools such as Achilles and
  DQD read.
- `cdm_version_concept_id` is 902984, OMOP CDM Version 5.5.0.
- `source_release_date` should be the release date of the MIMIC-IV version converted; it falls back
  to the CDM release date when `--source-release-date` is not given (MIMIC-IV's files do not
  carry their version).

## Fields

| Field | Source / logic |
|---|---|
| `cdm_source_name` | `MIMIC-IV` and `--source-version` |
| `cdm_source_abbreviation` | `MIMIC-IV` |
| `cdm_holder` | `--cdm-holder` (default `not specified`) |
| `source_description` | a fixed description of MIMIC-IV, including the date shifting |
| `source_documentation_reference` | `https://mimic.mit.edu/docs/iv/` |
| `cdm_etl_reference` | `mimic-omop <version> (git <commit>)`; the commit is marked `-dirty` when run with uncommitted changes |
| `source_release_date` | `--source-release-date`, else the CDM release date |
| `cdm_release_date` | the day the ETL ran |
| `cdm_version` | `5.5` |
| `cdm_version_concept_id` | 902984 |
| `vocabulary_version` | Athena's vocabulary release (`v5.0 29-AUG-26`) |
| `cdm_release_identifier` | the CDM release date and the ETL reference |
