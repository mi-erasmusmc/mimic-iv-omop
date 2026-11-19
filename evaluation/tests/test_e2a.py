from pathlib import Path

import polars as pl
import pytest

from mimic_eval import compare, e2a, mimic_code

DATA = Path(__file__).resolve().parents[2] / "data"
DEMO = {
    "source": DATA / "demo-parquet",
    "cdm": DATA / "demo-cdm",
    "vocab": DATA / "vocab",
    "mimic_code": DATA / "eval/vendor/mimic-code",
}


def test_kdigo_creatinine_stages_follow_mimic_code():
    df = pl.DataFrame(
        {
            "creat": [1.0, 1.4, 1.6, 2.1, 3.1, 4.1, 0.9],
            "creat_low_past_48hr": [None, 1.0, 1.5, None, None, 3.7, None],
            "creat_low_past_7day": [None, 1.0, 1.0, 1.0, 1.0, 3.5, None],
        }
    )
    # 1.4: +0.4 in 48h -> 1; 1.6: 1.6x the 7-day low -> 1; 2.1: 2.1x -> 2; 3.1: 3.1x -> 3;
    # 4.1: >= 4 and +0.3 in 48h -> 3; no baseline -> 0
    assert df.select(e2a.aki_stage_creat()).to_series().to_list() == [0, 1, 1, 2, 3, 3, 0]


def test_values_classifies_each_key():
    source = pl.DataFrame({"k": [1, 2, 3, 4], "x": [1.0, 2.0, None, 5.0]})
    cdm = pl.DataFrame({"k": [1, 2, 3, 5], "x": [1.0, 2.5, 3.0, 1.0]})
    r = compare.values(source, cdm, ["k"], ["x"]).row(0, named=True)
    # key 3 has a value on the CDM only, key 5 is on the CDM only
    assert (r["identical"], r["different"], r["source_only"], r["cdm_only"]) == (1, 1, 1, 2)
    assert r["median_abs_diff"] == 0.5


def test_flags_counts_disagreement_and_kappa():
    source = pl.DataFrame({"k": [1, 2, 3, 4], "f": [1, 1, 0, 0]})
    cdm = pl.DataFrame({"k": [1, 2, 3], "f": [1, 0, 0]})  # key 4 missing on the CDM: 0
    r = compare.flags(source, cdm, ["k"], ["f"]).row(0, named=True)
    assert (r["source_only"], r["cdm_only"], r["agreement_pct"]) == (1, 0, 75.0)
    assert r["kappa"] == 0.5


@pytest.mark.skipif(not all(p.exists() for p in DEMO.values()), reason="needs the demo CDM")
def test_demo_charlson_from_source_values_and_creatinine_agree(tmp_path):
    files = mimic_code.sql_files(DEMO["mimic_code"], mimic_code.CHARLSON)
    assert all(f.exists() for f in files.values())
    out = e2a.run(DEMO["source"], DEMO["cdm"], DEMO["vocab"], DEMO["mimic_code"], tmp_path)
    ch = out["charlson"].filter(pl.col("variant") == "cdm")
    assert ch["agreement_pct"].min() == 100.0
    assert out["kdigo_creatinine"]["identical_pct"].min() == 100.0
    assert out["kdigo_stage"]["agreement_pct"].item() == 100.0
