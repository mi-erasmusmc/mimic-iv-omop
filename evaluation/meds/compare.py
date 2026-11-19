"""E3a measures: the same model on the source and on the CDM, on the same held-out samples.

Each side's predictions (subject_id, prediction_time, boolean_value,
predicted_boolean_probability) are paired by subject and prediction time. Per side: AUROC, AUPRC,
calibration intercept and slope (logistic recalibration), and the integrated calibration index
(mean absolute difference between the predicted risk and an isotonic fit of the outcome on it).
The difference CDM minus source of each metric has a percentile interval from a bootstrap over
subjects (a subject's samples are drawn together); AUROC is "equivalent" when the 90% interval of
the difference lies within the margin (two one-sided tests at 5%; docs/evaluation.md, E3a).

Usage: uv run python compare.py <source predictions> <cdm predictions>
"""

import sys

import numpy as np
import polars as pl
from sklearn.isotonic import IsotonicRegression
from sklearn.linear_model import LogisticRegression
from sklearn.metrics import average_precision_score, roc_auc_score

MARGIN = 0.01
REPLICATES = 2000
KEY = ["subject_id", "prediction_time"]


def calibration(y: np.ndarray, p: np.ndarray) -> tuple[float, float, float]:
    p = np.clip(p, 1e-6, 1 - 1e-6)
    logit = np.log(p / (1 - p)).reshape(-1, 1)
    fit = LogisticRegression(C=np.inf).fit(logit, y)
    observed = IsotonicRegression(out_of_bounds="clip").fit(p, y).predict(p)
    return float(fit.intercept_[0]), float(fit.coef_[0, 0]), float(np.mean(np.abs(p - observed)))


def metrics(y: np.ndarray, p: np.ndarray) -> dict[str, float]:
    intercept, slope, ici = calibration(y, p)
    return {
        "auroc": roc_auc_score(y, p),
        "auprc": average_precision_score(y, p),
        "calibration_intercept": intercept,
        "calibration_slope": slope,
        "ici": ici,
    }


def paired(source: pl.DataFrame, cdm: pl.DataFrame) -> pl.DataFrame:
    s = source.select(*KEY, y="boolean_value", p_source="predicted_boolean_probability")
    c = cdm.select(*KEY, y_cdm="boolean_value", p_cdm="predicted_boolean_probability")
    both = s.join(c, on=KEY)
    if both.height != s.height or both.height != c.height:
        raise ValueError(f"unpaired samples: {s.height} source, {c.height} cdm, {both.height} both")
    if not (both["y"] == both["y_cdm"]).all():
        raise ValueError("labels differ between the sides")
    return both


def compare(source: pl.DataFrame, cdm: pl.DataFrame, seed: int = 0) -> pl.DataFrame:
    both = paired(source, cdm)
    y, ps, pc = (both[c].to_numpy() for c in ("y", "p_source", "p_cdm"))
    y = y.astype(int)
    point = {"source": metrics(y, ps), "cdm": metrics(y, pc)}
    subjects = both["subject_id"].to_numpy()
    ids, inverse = np.unique(subjects, return_inverse=True)
    rows = [np.flatnonzero(inverse == i) for i in range(len(ids))]
    rng = np.random.default_rng(seed)
    diffs = {k: [] for k in point["source"]}
    for _ in range(REPLICATES):
        idx = np.concatenate([rows[i] for i in rng.integers(0, len(ids), len(ids))])
        if y[idx].min() == y[idx].max():
            continue
        ms, mc = metrics(y[idx], ps[idx]), metrics(y[idx], pc[idx])
        for k in diffs:
            diffs[k].append(mc[k] - ms[k])
    out = []
    for k in diffs:
        d = np.asarray(diffs[k])
        out.append(
            {
                "metric": k,
                "source": point["source"][k],
                "cdm": point["cdm"][k],
                "difference": point["cdm"][k] - point["source"][k],
                "ci95_low": np.quantile(d, 0.025),
                "ci95_high": np.quantile(d, 0.975),
                "ci90_low": np.quantile(d, 0.05),
                "ci90_high": np.quantile(d, 0.95),
            }
        )
    result = pl.DataFrame(out).with_columns(
        equivalent=pl.when(pl.col("metric") == "auroc").then(
            (pl.col("ci90_low") > -MARGIN) & (pl.col("ci90_high") < MARGIN)
        )
    )
    corr = float(np.corrcoef(ps, pc)[0, 1])
    top_s, top_c = (p >= np.quantile(p, 0.9) for p in (ps, pc))
    overlap = float((top_s & top_c).sum() / max(top_s.sum(), 1))
    return result.with_columns(
        samples=pl.lit(len(y)),
        positives=pl.lit(int(y.sum())),
        risk_correlation=pl.lit(corr),
        top_decile_overlap=pl.lit(overlap),
    )


if __name__ == "__main__":
    with pl.Config(tbl_cols=-1, tbl_width_chars=250):
        print(compare(pl.read_parquet(sys.argv[1]), pl.read_parquet(sys.argv[2])))
