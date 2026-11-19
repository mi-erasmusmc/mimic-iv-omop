# episode_event

**Source:** the [episodes](episode.md) and the condition records made from the same diagnosis
rows.
**Code:** `src/mimic_omop/etl/episode.py` (`episode_event`).

## Conventions

- Each episode is linked to the condition record(s) of its source row: 4 demo rows; 2,894 in
  MIMIC-IV 3.1 (2 episodes have two conditions).

## Fields

| Field | Source / logic |
|---|---|
| `episode_id` | the episode |
| `event_id` | `condition_occurrence_id` of the condition from the same source row |
| `episode_event_field_concept_id` | 1147127 (condition_occurrence.condition_occurrence_id) |
