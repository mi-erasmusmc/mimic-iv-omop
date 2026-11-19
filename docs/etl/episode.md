# episode

**Source:** coded records whose standard concept is in the Episode domain (see
[general](../general.md#coded-records-are-routed-by-domain)): the disease dynamics of diagnosis
codes, from `hosp.diagnoses_icd` and MIMIC-IV-ED `diagnosis`.
**Code:** `src/mimic_omop/etl/episode.py`.

## Conventions

- ICD codes for a disease in remission or relapse (ICD-10-CM C92.01 'Acute myeloid leukemia, in
  remission', C92.02 '... in relapse', ICD-9-CM 205.01) map in Athena to two concepts: the
  disease, a Condition, and its dynamic, 'Remission' (32945) or 'Progression' (32949), in the
  Episode domain. The condition is written to condition_occurrence as usual; the dynamic becomes
  an episode here.
- **OHDSI oncology convention:** a disease dynamic is an episode whose `episode_object_concept_id`
  is the disease, linked to the condition record through [episode_event](episode_event.md). The
  disease is the condition made from the same source row (the same event id // 10); 2 MIMIC-IV
  3.1 rows give two condition records, and the first is the object.
- The episode is dated like its condition: diagnoses at the start of their visit, ED diagnoses at
  the ED stay. MIMIC gives no end date, and there is no parent disease episode or episode number:
  these would need the oncology history that MIMIC does not have.
- 4 demo episodes; in MIMIC-IV 3.1, 2,892 (1,738 Remission, 1,154 Progression; 2,872 from
  `diagnoses_icd`, 20 from ED diagnoses), mostly leukemias and myeloma.

## Fields

| Field | Source / logic |
|---|---|
| `episode_id` | the event id of the diagnosis row's Episode target (`prefix * 10^15 + key * 10 + k`, see [condition_occurrence](condition_occurrence.md)) |
| `person_id` | `subject_id` |
| `episode_concept_id` | 32945 Remission or 32949 Progression |
| `episode_start_date` | as `condition_start_date` |
| `episode_start_datetime` | as `condition_start_datetime` |
| `episode_end_date` | not populated |
| `episode_end_datetime` | not populated |
| `episode_parent_id` | not populated |
| `episode_number` | not populated |
| `episode_object_concept_id` | `condition_concept_id` of the condition record from the same source row, else 0 |
| `episode_type_concept_id` | as `condition_type_concept_id` (32821 EHR billing record for `diagnoses_icd`) |
| `episode_source_value` | `icd_code` |
| `episode_source_concept_id` | Athena concept of the code |
