"""Thread budget for the E3 jobs on a shared machine.

The jobs run many worker processes (ACES shards, MEDS-Tab workers), and Polars, OpenMP, BLAS and
XGBoost each size their thread pools to every core by default: 32 workers on 192 cores made
14,000 threads. Every step therefore runs with `env(workers)`: CPUS cores in all (E3A_CPUS,
default 64), split evenly between the workers.
"""

import os

CPUS = int(os.environ.get("E3A_CPUS", "64"))
VARIABLES = (
    "POLARS_MAX_THREADS",
    "OMP_NUM_THREADS",
    "OPENBLAS_NUM_THREADS",
    "MKL_NUM_THREADS",
    "NUMEXPR_MAX_THREADS",
    "XGBOOST_NTHREAD",
)


def env(workers: int = 1) -> dict[str, str]:
    """The environment for a step with `workers` parallel processes."""
    per = str(max(1, CPUS // max(1, workers)))
    return os.environ | {v: per for v in VARIABLES}


def workers(n: int) -> int:
    """At most n workers, and no more than the budget's cores."""
    return max(1, min(n, CPUS))
