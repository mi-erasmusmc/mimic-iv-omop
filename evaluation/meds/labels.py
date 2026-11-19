"""Task labels for evaluation E3 with ACES, as MEDS-DEV's `meds-dev-task` extracts them, for
MEDS-DEV's tasks and for this evaluation's own (evaluation/tasks/).

Usage: uv run python labels.py <MEDS dataset dir> <output dir> [task ...]
Labels go to <output>/<task>/<split>/<shard>.parquet. Shards run in parallel (hydra joblib).
"""

import subprocess
import sys
from pathlib import Path

import threads
from MEDS_DEV import DATASETS, TASKS

LOCAL = Path(__file__).parents[1] / "tasks"
LOCAL_TASKS = {
    "readmission/general_hospital/30d": LOCAL / "readmission_30d.yaml",
    "mortality/in_hospital/first_24h": LOCAL / "in_hospital_mortality_first_24h.yaml",
}
DEFAULT = [
    "mortality/in_icu/first_24h",
    "mortality/in_hospital/first_24h",
    "readmission/general_hospital/30d",
    *sorted(t for t in TASKS if t.startswith("abnormal_lab/")),
]


def config_path(task: str) -> Path:
    return LOCAL_TASKS[task] if task in LOCAL_TASKS else Path(TASKS[task]["criteria_fp"])


def extract(dataset: Path, out: Path, task: str, jobs: int = 16) -> None:
    jobs = threads.workers(jobs)
    predicates = DATASETS["MIMIC-IV"]["predicates"]
    target = out / task
    cmd = (
        f"aces-cli --multirun cohort_name={task.replace('/', '_')} data=sharded data.standard=meds "
        f"data.root={dataset}/data data.shard=$(expand_shards {dataset}/data) "
        f"config_path={config_path(task)} predicates_path={predicates} "
        f"'output_filepath={target}/${{data._prefix}}.parquet' log_dir={target}/.logs "
        f"hydra/launcher=joblib hydra.launcher.n_jobs={jobs}"
    )
    subprocess.run(cmd, shell=True, check=True, env=threads.env(jobs))


if __name__ == "__main__":
    dataset, out = Path(sys.argv[1]), Path(sys.argv[2])
    for task in sys.argv[3:] or DEFAULT:
        print(task, flush=True)
        extract(dataset, out, task)
