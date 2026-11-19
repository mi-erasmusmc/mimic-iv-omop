"""E2b: OHDSI cohort definitions evaluated the same way on the CDM and on MIMIC-IV.

`cohort` implements the subset of Circe (the OHDSI cohort definition language) that the E2b
cohorts use, following the SQL CirceR generates: concept sets; condition, procedure, drug,
measurement and visit criteria; criteria groups with occurrence counts in windows relative to
the index event; inclusion rules; first-event limits; a date-offset end; era collapse. Anything
else raises `Unsupported`, so the evaluator never silently differs from Circe.

It runs on `Data`: events per criteria domain with standard concept ids, and observation
periods. `cdm_data` reads them from the CDM. `source_data` builds them from MIMIC-IV: each
source row gets the standard concepts its code maps to (diagnosis, procedure and NDC codes
through Athena, looked up here; lab, ICU item and drug name codes through CVB as the ETL
resolves it), and MIMIC has no observation periods. Running the same definition on both, with
the same code, makes the difference a measure of the ETL (docs/evaluation.md, E2b).
"""

import json
from dataclasses import dataclass
from datetime import date
from pathlib import Path

import polars as pl

# Circe criteria type -> Data.events key
DOMAINS = {
    "ConditionOccurrence": "condition",
    "ProcedureOccurrence": "procedure",
    "DrugExposure": "drug",
    "Measurement": "measurement",
    "VisitOccurrence": "visit",
}
# criteria attributes the evaluator implements; the *TypeExclude flags only when false
SUPPORTED = {"CodesetId", "First", "ValueAsNumber", "Unit", "CorrelatedCriteria"}
TYPE_EXCLUDES = {"ConditionTypeExclude", "ProcedureTypeExclude", "DrugTypeExclude",
                 "MeasurementTypeExclude", "VisitTypeExclude"}  # fmt: skip
EVENT_COLUMNS = {
    "person_id": pl.Int64,
    "event_id": pl.Int64,
    "concept_id": pl.Int64,
    "start_date": pl.Date,
    "end_date": pl.Date,
    "visit_id": pl.Int64,
    "value_as_number": pl.Float64,
    "unit_concept_id": pl.Int64,
}
NO_START, NO_END = date(1, 1, 1), date(9999, 12, 31)  # an observation period without bounds
ONE_DAY = pl.duration(days=1)


class Unsupported(Exception):
    """A cohort definition uses Circe logic the evaluator does not implement."""


@dataclass
class Data:
    events: dict[str, pl.LazyFrame]  # criteria domain -> EVENT_COLUMNS
    periods: pl.LazyFrame | None  # person_id, op_start, op_end; None: none (all time observed)


# ---------------------------------------------------------------------------------- concept sets


def concept_sets(
    definition: dict, concept: pl.LazyFrame, ancestor: pl.LazyFrame, relationship: pl.LazyFrame
) -> pl.DataFrame:
    """(codeset_id, concept_id) of a definition's concept sets, resolved as Circe does: the
    listed concepts, their valid descendants where asked, and the source concepts that map to
    them where asked; less the excluded concepts, resolved the same way."""
    valid = concept.filter(pl.col("invalid_reason").is_null()).select("concept_id")

    def resolve(items: list[dict]) -> set[int]:
        ids = {i["concept"]["CONCEPT_ID"] for i in items}
        out = set(ids)
        desc = [i["concept"]["CONCEPT_ID"] for i in items if i.get("includeDescendants")]
        if desc:
            out |= set(
                ancestor.filter(pl.col("ancestor_concept_id").is_in(desc))
                .join(valid, left_on="descendant_concept_id", right_on="concept_id")
                .select("descendant_concept_id")
                .collect()
                .to_series()
            )
        mapped = [i["concept"]["CONCEPT_ID"] for i in items if i.get("includeMapped")]
        if mapped:
            out |= set(
                relationship.filter(
                    pl.col("relationship_id") == "Maps to",
                    pl.col("invalid_reason").is_null(),
                    pl.col("concept_id_2").is_in(mapped),
                )
                .select("concept_id_1")
                .collect()
                .to_series()
            )
        return out

    rows = []
    for cs in definition["ConceptSets"]:
        items = cs["expression"]["items"]
        ids = resolve([i for i in items if not i.get("isExcluded")])
        ids -= resolve([i for i in items if i.get("isExcluded")])
        rows += [(cs["id"], c) for c in sorted(ids)]
    return pl.DataFrame(rows, schema={"codeset_id": pl.Int64, "concept_id": pl.Int64}, orient="row")


# ------------------------------------------------------------------------------------- criteria


def _criteria_events(criteria: dict, data: Data, codesets: pl.DataFrame) -> pl.DataFrame:
    """Events of one criteria: person_id, event_id, start_date, end_date, visit_id."""
    ((kind, c),) = criteria.items()
    if kind not in DOMAINS:
        raise Unsupported(f"criteria {kind}")
    unknown = set(c) - SUPPORTED - TYPE_EXCLUDES
    if unknown or any(c.get(k) for k in TYPE_EXCLUDES):
        raise Unsupported(f"{kind} attributes {sorted(unknown) or 'type exclusion'}")
    lf = data.events[DOMAINS[kind]]
    if "CodesetId" in c:
        ids = codesets.filter(pl.col("codeset_id") == c["CodesetId"])["concept_id"].to_list()
        lf = lf.filter(pl.col("concept_id").is_in(ids))
    # Circe's end date of a record, by domain
    end = {
        "ConditionOccurrence": pl.coalesce("end_date", pl.col("start_date") + ONE_DAY),
        "ProcedureOccurrence": pl.col("start_date") + ONE_DAY,
        "DrugExposure": pl.coalesce("end_date", pl.col("start_date") + ONE_DAY),
        "Measurement": pl.col("start_date") + ONE_DAY,
        "VisitOccurrence": pl.col("end_date"),
    }[kind]
    lf = lf.with_columns(end_date=end)
    if "ValueAsNumber" in c:
        lf = lf.filter(_numeric(pl.col("value_as_number"), c["ValueAsNumber"]))
    if "Unit" in c:
        lf = lf.filter(pl.col("unit_concept_id").is_in([u["CONCEPT_ID"] for u in c["Unit"]]))
    if c.get("First"):
        lf = lf.filter(
            pl.int_range(pl.len()).over("person_id", order_by=["start_date", "event_id"]) == 0
        )
    events = lf.select("person_id", "event_id", "start_date", "end_date", "visit_id").collect()
    if c.get("CorrelatedCriteria"):
        index = _with_periods(events, data)
        keep = _group(index, c["CorrelatedCriteria"], data, codesets)
        events = events.join(keep, on=["person_id", "event_id"], how="semi")
    return events


def _numeric(x: pl.Expr, r: dict) -> pl.Expr:
    v, op = r["Value"], r["Op"]
    ops = {
        "lt": x < v, "lte": x <= v, "gt": x > v, "gte": x >= v, "eq": x == v, "!eq": x != v,
        "bt": (x >= v) & (x <= r.get("Extent", v)), "!bt": ~((x >= v) & (x <= r.get("Extent", v))),
    }  # fmt: skip
    if op not in ops:
        raise Unsupported(f"numeric operator {op}")
    return ops[op]


def _with_periods(events: pl.DataFrame, data: Data, prior: int = 0, post: int = 0) -> pl.DataFrame:
    """Events with the observation period they start in (op_start, op_end), those in none
    dropped; prior/post: days of observation required before and after the start."""
    if data.periods is None:
        return events.with_columns(op_start=pl.lit(NO_START), op_end=pl.lit(NO_END))
    periods = data.periods.filter(
        pl.col("person_id").is_in(events["person_id"].unique().implode())
    ).collect()
    return events.join(periods, on="person_id").filter(
        pl.col("start_date").is_between("op_start", "op_end"),
        pl.col("op_start") + pl.duration(days=prior) <= pl.col("start_date"),
        pl.col("start_date") + pl.duration(days=post) <= pl.col("op_end"),
    )


# ------------------------------------------------------------------------------- criteria groups


def _bound(window: dict, side: str, index: pl.Expr, ignore_op: bool) -> pl.Expr | None:
    """The date bound of one side of a window: index + days * coeff, or without days the
    observation period's start or end (none if the criterion ignores observation periods)."""
    b = window.get(side, {})
    coeff = b.get("Coeff", -1 if side == "Start" else 1)
    if "Days" in b and b["Days"] is not None:
        return index + pl.duration(days=b["Days"] * coeff)
    if ignore_op:
        return None
    return pl.col("op_start") if coeff < 0 else pl.col("op_end")


def _window(window: dict, event_date: str, ignore_op: bool) -> pl.Expr:
    if not window:
        return pl.lit(True)
    index = pl.col("end_date" if window.get("UseIndexEnd") else "start_date")
    x = pl.col(event_date)
    cond = pl.lit(True)
    lo, hi = _bound(window, "Start", index, ignore_op), _bound(window, "End", index, ignore_op)
    if lo is not None:
        cond &= x >= lo
    if hi is not None:
        cond &= x <= hi
    return cond


def _correlated(
    index: pl.DataFrame, item: dict, data: Data, codesets: pl.DataFrame
) -> pl.DataFrame:
    """Index events (person_id, event_id) satisfying one correlated criteria's occurrence rule."""
    occ = item.get("Occurrence", {"Type": 2, "Count": 1})
    if occ.get("IsDistinct"):
        raise Unsupported("distinct occurrence counts")
    ignore_op = item.get("IgnoreObservationPeriod", False)
    events = _criteria_events(item["Criteria"], data, codesets).rename(
        {"start_date": "a_start", "end_date": "a_end", "visit_id": "a_visit", "event_id": "a_id"}
    )
    start_w, end_w = item.get("StartWindow") or {}, item.get("EndWindow") or {}
    cond = pl.lit(True)
    if not ignore_op:
        cond &= pl.col("a_start").is_between("op_start", "op_end")
    # a start window compares the event's start unless told otherwise, an end window its end
    cond &= _window(start_w, "a_end" if start_w.get("UseEventEnd") else "a_start", ignore_op)
    cond &= _window(end_w, "a_start" if end_w.get("UseEventEnd") is False else "a_end", ignore_op)
    if item.get("RestrictVisit"):
        cond &= pl.col("a_visit") == pl.col("visit_id")
    matched = (
        index.join(events, on="person_id")
        .filter(cond)
        .group_by("person_id", "event_id")
        .agg(n=pl.len())
    )
    counts = (
        index.select("person_id", "event_id")
        .join(matched, on=["person_id", "event_id"], how="left")
        .with_columns(pl.col("n").fill_null(0))
    )
    n, k = pl.col("n"), occ["Count"]
    rule = {0: n == k, 1: n <= k, 2: n >= k}[occ["Type"]]
    return counts.filter(rule).select("person_id", "event_id")


def _group(index: pl.DataFrame, group: dict, data: Data, codesets: pl.DataFrame) -> pl.DataFrame:
    """Index events satisfying a criteria group (ALL, ANY, AT_LEAST n, AT_MOST n of its parts)."""
    if group.get("DemographicCriteriaList"):
        raise Unsupported("demographic criteria")
    parts = [_correlated(index, item, data, codesets) for item in group.get("CriteriaList", [])]
    parts += [_group(index, g, data, codesets) for g in group.get("Groups", [])]
    keys = index.select("person_id", "event_id")
    if not parts:
        return keys
    hits = pl.concat(parts).group_by("person_id", "event_id").agg(n=pl.len())
    counts = keys.join(hits, on=["person_id", "event_id"], how="left").with_columns(
        pl.col("n").fill_null(0)
    )
    kind, k = group.get("Type", "ALL"), group.get("Count", 0)
    rule = {"ALL": pl.col("n") == len(parts), "ANY": pl.col("n") > 0,
            "AT_LEAST": pl.col("n") >= k, "AT_MOST": pl.col("n") <= k}  # fmt: skip
    if kind not in rule:
        raise Unsupported(f"group type {kind}")
    return counts.filter(rule[kind]).select("person_id", "event_id")


# ---------------------------------------------------------------------------------------- cohort


def _first(events: pl.DataFrame, limit: dict | None) -> pl.DataFrame:
    if (limit or {}).get("Type", "All") == "All":
        return events
    if limit["Type"] != "First":
        raise Unsupported(f"limit {limit['Type']}")
    return events.filter(
        pl.int_range(pl.len()).over("person_id", order_by=["start_date", "event_id"]) == 0
    )


def cohort(definition: dict, data: Data, codesets: pl.DataFrame) -> pl.DataFrame:
    """The cohort's periods: person_id, cohort_start_date, cohort_end_date."""
    for key in ("CensoringCriteria", "CensorWindow", "DateAdjustment"):
        if definition.get(key):
            raise Unsupported(key)
    pc = definition["PrimaryCriteria"]
    window = pc.get("ObservationWindow", {})
    primary = pl.concat(
        [_criteria_events(c, data, codesets) for c in pc["CriteriaList"]], how="vertical_relaxed"
    )
    primary = _with_periods(primary, data, window.get("PriorDays", 0), window.get("PostDays", 0))
    # Circe renumbers the primary events per person by start date
    primary = primary.with_columns(
        event_id=pl.int_range(pl.len()).over("person_id", order_by=["start_date", "event_id"])
    )
    events = _first(primary, pc.get("PrimaryCriteriaLimit"))
    if definition.get("AdditionalCriteria"):
        keep = _group(events, definition["AdditionalCriteria"], data, codesets)
        events = events.join(keep, on=["person_id", "event_id"], how="semi")
        # Circe applies the qualifying limit only to events that additional criteria qualified
        events = _first(events, definition.get("QualifiedLimit"))
    for rule in definition.get("InclusionRules", []):
        keep = _group(events, rule["expression"], data, codesets)
        events = events.join(keep, on=["person_id", "event_id"], how="semi")
    events = _first(events, definition.get("ExpressionLimit"))

    strategy = definition.get("EndStrategy") or {}
    if "DateOffset" in strategy:
        offset = strategy["DateOffset"]
        field = "start_date" if offset["DateField"] == "StartDate" else "end_date"
        end = pl.min_horizontal(pl.col(field) + pl.duration(days=offset["Offset"]), "op_end")
    elif strategy:
        raise Unsupported(f"end strategy {sorted(strategy)}")
    else:
        end = pl.col("op_end")
    periods = events.select("person_id", "start_date", end_date=end)
    return _collapse(periods, (definition.get("CollapseSettings") or {}).get("EraPad", 0))


def _collapse(periods: pl.DataFrame, pad: int) -> pl.DataFrame:
    """Merge periods that overlap once their ends are extended by `pad` days (Circe's era)."""
    padded = periods.with_columns(pl.col("end_date") + pl.duration(days=pad)).sort(
        "person_id", "start_date", "end_date"
    )
    prior_end = pl.col("end_date").cum_max().shift(1).over("person_id")
    eras = padded.with_columns(
        era=(prior_end.is_null() | (prior_end < pl.col("start_date"))).cast(pl.Int64).cum_sum()
    )
    return (
        eras.group_by("person_id", "era")
        .agg(
            cohort_start_date=pl.col("start_date").min(),
            cohort_end_date=pl.col("end_date").max() - pl.duration(days=pad),
        )
        .select("person_id", "cohort_start_date", "cohort_end_date")
        .sort("person_id", "cohort_start_date")
    )


def load_definition(path: Path) -> dict:
    return json.loads(Path(path).read_text())
