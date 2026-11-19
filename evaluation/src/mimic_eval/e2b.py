"""E2b: OHDSI cohorts on the CDM and on the source (docs/evaluation.md).

For each cohort, from the definitions and Circe's output that evaluation/R/generate_cohorts.R
wrote to `circe_dir`:
1. Circe (CohortGenerator) on the CDM: the official result;
2. the evaluator (`cohorts`) on the CDM, which must reproduce 1;
3. the evaluator on MIMIC-IV, the same code and concept sets on the source, without observation
   periods (MIMIC has none) and again with the CDM's, to separate their effect.
Then 3 is compared with 2: persons and entries, and their agreement.
"""

import logging
from pathlib import Path

import polars as pl

from mimic_eval import cohort_data, cohorts

log = logging.getLogger(__name__)
KEY = ["person_id", "cohort_start_date"]


def circe(circe_dir: Path, cohort_id: int) -> pl.DataFrame:
    return pl.read_csv(Path(circe_dir) / f"cohort_{cohort_id}.csv", try_parse_dates=True).select(
        person_id=pl.col("subject_id").cast(pl.Int64),
        cohort_start_date=pl.col("cohort_start_date").cast(pl.Date),
        cohort_end_date=pl.col("cohort_end_date").cast(pl.Date),
    )


def same(a: pl.DataFrame, b: pl.DataFrame) -> dict:
    """Rows of two cohorts that the other lacks."""
    cols = ["person_id", "cohort_start_date", "cohort_end_date"]
    return {
        "only_first": a.join(b, on=cols, how="anti").height,
        "only_second": b.join(a, on=cols, how="anti").height,
    }


def agreement(source: pl.DataFrame, cdm: pl.DataFrame, persons: int) -> dict:
    """Person- and entry-level agreement of the CDM cohort with the source cohort as reference;
    `persons` is the population (the CDM's persons) for kappa."""
    s, c = set(source["person_id"]), set(cdm["person_id"])
    both = len(s & c)
    a, b, n = len(s), len(c), persons
    po = (n - a - b + 2 * both) / n
    pe = (a * b + (n - a) * (n - b)) / n**2
    exact = cdm.join(source, on=KEY, how="semi").height
    near = (
        cdm.join(source.select("person_id", s_start="cohort_start_date"), on="person_id")
        .filter((pl.col("cohort_start_date") - pl.col("s_start")).dt.total_days().abs() <= 1)
        .select(KEY)
        .unique()
        .height
    )
    matched = cdm.join(source, on=KEY, suffix="_source")
    return {
        "source_persons": a,
        "cdm_persons": b,
        "both_persons": both,
        "jaccard": round(both / len(s | c), 4) if s | c else None,
        "kappa": round((po - pe) / (1 - pe), 4) if pe < 1 else None,
        "pabak": round(2 * po - 1, 4),
        "sensitivity": round(both / a, 4) if a else None,
        "ppv": round(both / b, 4) if b else None,
        "source_entries": source.height,
        "cdm_entries": cdm.height,
        "entries_same_start": exact,
        "entries_start_within_1d": near,
        "matched_same_end": matched.filter(
            pl.col("cohort_end_date") == pl.col("cohort_end_date_source")
        ).height,
    }


def run(
    cdm: Path, source: Path, vocab: Path, circe_dir: Path, ids: list[int], out: Path
) -> pl.DataFrame:
    out = Path(out)
    out.mkdir(parents=True, exist_ok=True)
    athena = lambda t: pl.scan_parquet(Path(vocab) / f"athena/{t}.parquet")  # noqa: E731
    definitions = {i: cohorts.load_definition(Path(circe_dir) / f"cohort_{i}.json") for i in ids}
    sets = {
        i: cohorts.concept_sets(
            d, athena("concept"), athena("concept_ancestor"), athena("concept_relationship")
        )
        for i, d in definitions.items()
    }
    needed = set(pl.concat(sets.values())["concept_id"])
    cdm_data = cohort_data.cdm_data(cdm)
    log.info("source events for %d concepts", len(needed))
    source_data = cohort_data.source_data(source, vocab, needed)
    persons = pl.scan_parquet(Path(cdm) / "person/*.parquet").select(pl.len()).collect().item()
    rows = []
    for i, d in definitions.items():
        log.info("cohort %d", i)
        official = circe(circe_dir, i)
        on_cdm = cohorts.cohort(d, cdm_data, sets[i])
        on_source = cohorts.cohort(d, source_data, sets[i])
        # the source with the CDM's observation periods: what remains is not the periods
        with_periods = cohorts.Data(source_data.events, cdm_data.periods)
        on_source_periods = cohorts.cohort(d, with_periods, sets[i])
        sides = {"cdm": on_cdm, "source": on_source, "source_cdm_periods": on_source_periods}
        for name, df in sides.items():
            df.write_parquet(out / f"cohort_{i}_{name}.parquet")
        check = same(official, on_cdm)
        for name in ("source", "source_cdm_periods"):
            rows.append(
                {
                    "cohort_id": i,
                    "source_side": name,
                    "circe_entries": official.height,
                    "evaluator_vs_circe_only_circe": check["only_first"],
                    "evaluator_vs_circe_only_evaluator": check["only_second"],
                    **agreement(sides[name], on_cdm, persons),
                }
            )
    result = pl.DataFrame(rows)
    result.write_csv(out / "e2b.csv")
    return result
