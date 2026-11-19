from datetime import date

import polars as pl

from mimic_omop.etl import episode
from mimic_omop.etl.events import EVENT_SCHEMA

REMISSION, AML = 32945, 140352
ROW = 1 * 10**15 + 20_000_001_0050  # diagnoses_icd row: hadm 20000001, seq_num 5, ICD-10


def test_a_disease_dynamic_is_an_episode_of_the_condition_from_the_same_code():
    events = pl.LazyFrame(
        [{"event_id": ROW * 10, "person_id": 1, "concept_id": REMISSION, "domain_id": "Episode",
          "start_date": date(2150, 1, 1), "type_concept_id": 32821, "source_value": "C9201",
          "source_concept_id": 45552281}],
        schema=EVENT_SCHEMA,
    )  # fmt: skip
    conditions = pl.LazyFrame(
        {"condition_occurrence_id": [ROW * 10 + 1, (ROW + 1) * 10], "condition_concept_id": [AML, 201826]}
    )  # fmt: skip
    ep = episode.episode(events, conditions).collect()
    assert ep.select(
        "episode_id", "episode_concept_id", "episode_object_concept_id", "episode_start_date",
        "episode_type_concept_id", "episode_source_value",
    ).rows() == [(ROW * 10, REMISSION, AML, date(2150, 1, 1), 32821, "C9201")]  # fmt: skip
    links = episode.episode_event(ep.lazy(), conditions).collect()
    # the condition of the same source row only, as a condition_occurrence_id
    assert links.rows() == [(ROW * 10, ROW * 10 + 1, 1147127)]
