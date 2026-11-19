import json
import os
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parents[1]))
import e3  # noqa: E402


def test_seed_lr_freezes_the_latest_source_variances(tmp_path):
    dataset = tmp_path / "meds"
    (dataset / "data" / "train").mkdir(parents=True)
    (dataset / "data" / "train" / "0.parquet").write_bytes(b"x")
    root = tmp_path / "e3"
    task = next(iter(e3.tasks()))
    fits = e3.side_dir(root, "source", dataset) / f"predictions-{e3.features_hash()}" / task
    fits.mkdir(parents=True)
    old = fits / "cyclops_lr-aaa.txt"
    old.write_text("cyclops: 9 s, 3 of 10 features, variance [0.5]\n")
    os.utime(old, (time.time() - 100, time.time() - 100))
    (fits / "cyclops_lr-bbb.txt").write_text("cyclops: 9 s, 3 of 10 features, variance [0.0286]\n")
    (fits / "cyclops_lr_quintiles-ccc.txt").write_text("cyclops: 1 s, variance [1e-3] (cv)\n")

    e3.seed_lr(root, dataset)

    frozen = json.loads((e3.lr_params_dir(root) / f"{task}.json").read_text())
    assert frozen["cyclops_lr"] == 0.0286 and frozen["cyclops_lr_from"] == "cyclops_lr-bbb.txt"
    assert frozen["cyclops_lr_quintiles"] == 0.001
    assert e3.frozen_lr_variance(root, task, "cyclops_lr", final=False) == 0.0286
    assert e3.frozen_lr_variance(root, task, "cyclops_lr", final=True) is None
    assert e3.frozen_lr_variance(root, task, "cyclops_lr_counts", final=False) is None


def test_frozen_params_never_takes_another_kind_of_parameter(tmp_path):
    f = e3.features_hash()
    search = tmp_path / "params" / f"{f}-0123456789"
    search.mkdir(parents=True)
    (search / "t.json").write_text('{"num_leaves": 31}')
    time.sleep(0.01)
    e3.lr_params_dir(tmp_path).mkdir(parents=True)
    (e3.lr_params_dir(tmp_path) / "t.json").write_text('{"cyclops_lr": 0.1}')
    legacy = tmp_path / "params" / f"{f}-lr"  # the name LR variances first had
    legacy.mkdir()
    (legacy / "t.json").write_text('{"cyclops_lr": 0.1}')
    assert e3.frozen_params(tmp_path, "t", final=False) == search / "t.json"


def test_compare_pairs_only_the_fits_of_this_run(tmp_path, monkeypatch):
    import concurrent.futures

    import polars as pl

    class Pool(concurrent.futures.ThreadPoolExecutor):  # in-process, so the patches below hold
        def __init__(self, workers, mp_context=None):
            super().__init__(workers)

    task = next(iter(e3.tasks()))
    monkeypatch.setattr(e3, "SELECTED", [task])
    monkeypatch.setattr(e3, "cyclops_build", lambda: "3.7.1-0123456789")
    monkeypatch.setattr(concurrent.futures, "ProcessPoolExecutor", Pool)
    compared = []

    def compare_one(job):
        compared.append((job[1], job[2].name))
        cols = ("source", "cdm", "difference", "ci90_low", "ci90_high")
        return pl.DataFrame({"metric": ["auroc"], **{c: [0.5] for c in cols}, "equivalent": [True],
                             "task": [job[0]], "model": [job[1]]})  # fmt: skip

    monkeypatch.setattr(e3, "_compare_one", compare_one)
    sides = {}
    for side in ("source", "cdm"):
        sides[side] = tmp_path / side
        (sides[side] / "data" / "train").mkdir(parents=True)
        (sides[side] / "data" / "train" / "0.parquet").write_bytes(side.encode())
    e3.params_dir(tmp_path).mkdir(parents=True)
    (e3.params_dir(tmp_path) / f"{task}.json").write_text('{"num_leaves": 31}')
    planned = {model: name for _, model, name, _ in e3.planned_fits(tmp_path, False, False)}
    for side, dataset in sides.items():
        preds = e3.side_dir(tmp_path, side, dataset) / f"predictions-{e3.features_hash()}" / task
        preds.mkdir(parents=True)
        for name in [*planned.values(), "cyclops_lr-0000000000.parquet"]:  # and an earlier fit
            (preds / name).write_bytes(b"")

    e3.compare_sides(tmp_path, sides["source"], sides["cdm"], final=False)

    assert sorted(compared) == sorted(planned.items())


def test_label_ages_come_from_the_birth_event_in_label_order(tmp_path):
    from datetime import datetime

    import polars as pl

    dataset = tmp_path / "meds"
    (dataset / "data" / "train").mkdir(parents=True)
    pl.DataFrame({
        "subject_id": [2, 1, 1],
        "time": [datetime(2100, 1, 1), datetime(2050, 6, 1), datetime(2060, 1, 1)],
        "code": ["MEDS_BIRTH", "MEDS_BIRTH", "LAB//x"],
    }).write_parquet(dataset / "data" / "train" / "0.parquet")  # fmt: skip
    features = tmp_path / "side" / "features-x"
    features.mkdir(parents=True)
    (features.parent / "manifest.json").write_text(json.dumps({"dataset": str(dataset)}))
    labels = pl.DataFrame({
        "subject_id": [1, 3, 2],
        "prediction_time": [datetime(2080, 6, 1), datetime(2080, 6, 1), datetime(2130, 1, 2)],
    })  # fmt: skip
    (ages,) = e3.label_ages(features, [labels])
    assert abs(ages[0] - 30) < 0.01 and abs(ages[2] - 30) < 0.01
    assert ages[1] != ages[1]  # no birth event: NaN
    assert (features / "birth.parquet").exists()


def test_ladder_lrs_borrow_the_nearest_frozen_variance(tmp_path):
    e3.lr_params_dir(tmp_path).mkdir(parents=True)
    frozen = {"cyclops_lr": 0.1, "cyclops_lr_binary": 0.004, "cyclops_lr_quintiles": 0.002}
    (e3.lr_params_dir(tmp_path) / "t.json").write_text(json.dumps(frozen))
    v = {s: e3.frozen_lr_variance(tmp_path, "t", f"cyclops_lr_ladder_{s}", False)
         for s in e3.LADDER_STEPS}  # fmt: skip
    assert v == {"fe0": 0.004, "fe": 0.004, "fe_short": 0.004, "fe_values": 0.002, "full_age": 0.1}
    assert e3.is_lr("cyclops_lr_ladder_fe") and e3.is_lightgbm("lightgbm_ladder_fe")
