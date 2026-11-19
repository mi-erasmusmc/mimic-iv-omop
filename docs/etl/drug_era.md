# drug_era

**Source:** [drug_exposure](drug_exposure.md), and `concept_ancestor` for ingredients.
**Code:** `src/mimic_omop/etl/eras.py`.

## Conventions

The standard OHDSI drug era logic:

- Drug concepts are rolled up to their RxNorm or RxNorm Extension ingredients through
  `concept_ancestor`; a combination drug counts for each of its ingredients. Drugs without an
  ingredient (and concept 0) are left out.
- Overlapping exposures of an ingredient are first merged; merged exposures that start within 30
  days of the previous one's end then form one era.
- `gap_days` is the era's length minus the days its exposures cover.
- Exposures of all types count, orders and administrations alike, so an ordered and given drug
  contributes both; overlapping records merge, so this lengthens eras only where an order runs
  beyond its administrations ([review R17](../review.md#r17-orders-and-administrations-are-both-recorded)).
- `dose_era` is not built, since drug records carry no dose in OMOP form
  ([review R20](../review.md#r20-dose-and-quantity-are-not-populated)).

## Fields

| Field | Source / logic |
|---|---|
| `drug_era_id` | `person_id * 10^6 +` the era's position for that person (by ingredient, then start) |
| `person_id` | `person_id` |
| `drug_concept_id` | the ingredient concept |
| `drug_era_start_date` | earliest `drug_exposure_start_date` of the era |
| `drug_era_end_date` | latest `drug_exposure_end_date` of the era |
| `drug_exposure_count` | number of drug_exposure records in the era |
| `gap_days` | era length in days minus the days covered by its (merged) exposures |
