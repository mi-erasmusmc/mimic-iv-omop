# Vocabulary tables

**Source:** the Athena download loaded with `mimic-omop load-athena` (release 29-AUG-26), and
CVB's MIMIC4 vocabulary (`MIMIC/Ontology/*_delta.csv` at the pinned commit, fetched with
`mimic-omop fetch-cvb`).
**Code:** `src/mimic_omop/etl/vocabulary.py`. Built once, not per person bucket.

## Conventions

- The output CDM carries its full vocabulary, so it can be analysed without a separate vocabulary
  schema, and every concept id in the clinical tables resolves: the build checks foreign keys from
  every concept field into `concept`.
- CVB's MIMIC4 concepts (24,434, ids from 2,000,000,000) are added. They are the
  `*_source_concept_id` of MIMIC's local codes (lab, ICU and drug items, DRG descriptions, race and
  location strings). Where an id exists in Athena too, Athena's concept is kept.
- **Decision:** CVB's mapping relationships ('Maps to', 'Maps to value' and the reverse 'Mapped
  from', 'Value mapped from') are written as the ETL resolved them through Athena, not as CVB lists
  them, so the vocabulary says what the data does (see
  [general](../general.md#vocabularies-and-mapping), rules 2 and 3). CVB's standard MIMIC4
  concepts keep a 'Maps to' to themselves, except those the ETL maps to a broader Athena concept
  ([review R29](../review.md#r29-cvb-items-mapped-only-to-a-broader-concept)): they 'Map to'
  that concept instead and are written as non-standard, so that a standard concept always maps
  to itself. CVB's other relationships ('Is a', 'Has relat context', ...) are kept.
  Relationships to concepts that are not in `concept` are left out.
- CVB's standard MIMIC4 concepts are not used as targets in the clinical tables, which only hold
  Athena standard concepts; see [review R24](../review.md#r24-cvbs-standard-mimic4-concepts).
- Athena's `None` vocabulary row (the vocabulary release) is kept; CVB's version is recorded in
  [metadata](metadata.md).
- **Decision:** CVB's concepts use two domains that neither Athena nor CVB defines, 'Undefined'
  (74 concepts) and 'Waveform Metadata' (44); they are added to `domain` with domain_concept_id 0,
  rather than changing CVB's concepts. To report to CVB.

## Tables

| Table | Content |
|---|---|
| `concept` | Athena's concepts and CVB's MIMIC4 concepts |
| `concept_relationship` | Athena's relationships, CVB's non-mapping relationships, and the resolved MIMIC4 mappings with their reverses |
| `vocabulary` | Athena's vocabularies and CVB's `MIMIC4` |
| `concept_class` | Athena's concept classes and CVB's 39 MIMIC4 classes (`m4_lab_itemid`, ...) |
| `domain` | Athena's domains, and 'Undefined' and 'Waveform Metadata' for CVB's concepts |
| `relationship` | Athena's, as loaded |
| `concept_ancestor` | Athena's, as loaded (no ancestry for MIMIC4 concepts) |
| `concept_synonym` | Athena's, as loaded |
| `drug_strength` | Athena's, as loaded |
| `concept_metadata` | Athena's, as loaded (new in CDM 5.5) |
| `concept_relationship_metadata` | Athena's, as loaded (new in CDM 5.5) |
| `pack_content` | Athena's, as loaded (new in CDM 5.5) |
