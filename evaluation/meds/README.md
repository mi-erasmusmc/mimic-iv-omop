# MEDS tools for E3

ACES (labels) and MEDS-Tab (XGBoost on tabularized features) at the versions the evaluation
pins. They require Polars 1.x, so they live in their own uv project; `mimic-eval` (Polars 2)
writes and reads MEDS as plain parquet. See `docs/evaluation.md`, E3.

    cd evaluation/meds && uv sync

The third E3 run's Cyclops LRs (`e3.py`) used a development build of Cyclops, `ebb89849`: 55
commits past the commit pinned in `pyproject.toml`, with solver speed-ups that are not yet on
OHDSI/Cyclops. An L1 logistic regression at a fixed prior variance is a convex fit, so the pinned
version should reach the same models within solver tolerance, but this has not been checked. The
pin moves to a released Cyclops once those changes are merged.
