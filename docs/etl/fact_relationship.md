# fact_relationship

**Source:** the microbiology records built from `hosp.microbiologyevents` (see
[specimen](specimen.md)).
**Code:** `src/mimic_omop/etl/fact_relationship.py`, `src/mimic_omop/etl/microbiology.py`.

## Conventions

- **CDM:** fact_relationship holds relationships between records that no other field holds.
  Each microbiology test result is linked to its specimen, in both directions: the result 'Has
  specimen' (44818756) the specimen, and the specimen is 'Specimen of' (44818854) the result.
  In the demo: 1,989 test results, 3,978 rows.
- Susceptibilities are not linked here: they point at their isolate's test result through
  `measurement_event_id`, the CDM 5.4+ field for this, and reach the specimen through it.
- A test result is in the table its concept's domain gives it, usually measurement; Gram stains
  and some antigen tests map to Observation concepts, and a few tests to Procedure concepts, so
  `domain_concept_id_*` says which (21 Measurement, 27 Observation, 10 Procedure; 36 Specimen).

## Fields

| Field | Source / logic |
|---|---|
| `domain_concept_id_1` | 36 Specimen, or the domain of the test result |
| `fact_id_1` | `specimen_id`, or the test result's id |
| `domain_concept_id_2` | the other record's domain |
| `fact_id_2` | the other record's id |
| `relationship_concept_id` | 44818854 Specimen of (specimen first), 44818756 Has specimen (test result first) |
