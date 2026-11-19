"""episode and episode_event: coded events in the Episode domain (see etl/events.py).

In MIMIC-IV these are the disease dynamics of diagnosis codes such as ICD-10-CM C92.01 'Acute
myeloid leukemia, in remission', which Athena maps to two concepts: the disease (Condition) and
its dynamic, 'Remission' or 'Progression' (Episode). Following the OHDSI oncology convention, the
dynamic becomes an episode whose object is the disease, linked through episode_event to the
condition record made from the same source row (its event id // 10 is the same).

The episode is dated like the condition (the diagnosis date); MIMIC gives no end, no parent
disease episode and no episode number.
"""

import polars as pl

from mimic_omop.context import Inputs
from mimic_omop.etl.microbiology import ID_FIELD
from mimic_omop.etl.sources import domain_events


def _conditions(conditions: pl.LazyFrame) -> pl.LazyFrame:
    return conditions.select(
        row=pl.col("condition_occurrence_id") // 10,
        event_id="condition_occurrence_id",
        object=pl.col("condition_concept_id"),
    )


def episode(events: pl.LazyFrame, conditions: pl.LazyFrame) -> pl.LazyFrame:
    """One episode per Episode event; its object is the concept of the first condition record of
    the same source row (0 when there is none)."""
    objects = (
        _conditions(conditions).group_by("row").agg(pl.col("object").sort_by("event_id").first())
    )
    events = events.with_columns(row=pl.col("event_id") // 10)
    return events.join(objects, on="row", how="left").select(
        episode_id="event_id",
        person_id="person_id",
        episode_concept_id="concept_id",
        episode_start_date="start_date",
        episode_start_datetime="start_datetime",
        episode_end_date=pl.lit(None, pl.Date),
        episode_end_datetime=pl.lit(None, pl.Datetime("us")),
        episode_parent_id=pl.lit(None, pl.Int64),
        episode_number=pl.lit(None, pl.Int64),
        episode_object_concept_id=pl.col("object").fill_null(0),
        episode_type_concept_id="type_concept_id",
        episode_source_value="source_value",
        episode_source_concept_id="source_concept_id",
    )


def episode_event(episodes: pl.LazyFrame, conditions: pl.LazyFrame) -> pl.LazyFrame:
    """Each episode and the condition records of its source row."""
    return (
        episodes.select("episode_id", row=pl.col("episode_id") // 10)
        .join(_conditions(conditions), on="row")
        .select(
            "episode_id",
            "event_id",
            episode_event_field_concept_id=pl.lit(ID_FIELD["Condition"], pl.Int64),
        )
    )


def build(inp: Inputs) -> pl.LazyFrame:
    return episode(domain_events(inp, "Episode"), inp.cdm.scan("condition_occurrence"))


def build_event(inp: Inputs) -> pl.LazyFrame:
    return episode_event(inp.cdm.scan("episode"), inp.cdm.scan("condition_occurrence"))
