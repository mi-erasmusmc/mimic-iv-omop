"""E3 models on a feature matrix: LightGBM, Cyclops L1 logistic regression, and XGBoost.

- LightGBM (the main tree model): hyperparameters found once per task by `lightgbm_search`
  (N_TRIALS Optuna trials scored by the tuning split's AUROC) and then frozen: `lightgbm_fit` fits
  them on the training split, stopping early on the tuning split, identically on both sides. Trees
  use the columns with at least MIN_NONZERO training entries (`kept_columns`); with 167k columns
  and 567M entries (readmission) a fit takes minutes, so a search per ETL iteration would take
  hours.
- Every model's columns: those FeatureExtraction's tidyCovariateData drops as redundant are
  removed first (`redundant_columns`: constant columns, and one indicator of each set that
  partitions the rows, such as sex), as PLP does before every model.
- LR: Cyclops with a Laplace prior. Routine runs freeze each task's prior variance (e3.py
  seed-lr / frozen_lr_variance), as they freeze the LightGBM hyperparameters; final runs choose it
  by LR_FOLDS-fold cross-validation on the training split, the search starting at LR_START. (On the
  largest tasks that search reaches weak penalties whose working sets exceed prox-Newton's dense
  budget, where Cyclops falls back to coordinate descent and a fold fit takes hours.) Columns
  are scaled by their maximum absolute value on the training split, as PLP normalises; the
  `quintiles` variant also gives each value column indicators of its training quintiles (scaled
  raw values alone can be shrunk out by the prior).
- XGBoost (final runs, as a reference): MEDS-Tab's search space (eta, lambda and alpha
  log-uniform in [0.001, 1], subsample in [0.5, 1], min_child_weight in [0.01, 100], max_depth
  2-15, 100-999 rounds with early stopping after 1-9 rounds without improvement in the tuning
  log loss), N_TRIALS trials, on the CPU with a bounded histogram cache. MEDS-Tab also searches the
  code inclusion threshold; here it stays at featurize.MIN_COUNT. On a GPU, deep trees over this
  many columns ask for 64-96 GB.

- Decomposition of the gap between the LR and the trees (`--decompose`): the LR on code
  indicators only (`lr_inputs` "binary", PLP-like), on counts ("counts"), then on counts and raw
  values (`cyclops_lr`) and with quintiles (`cyclops_lr_quintiles`); LightGBM with stumps
  (`lightgbm_stumps`: additive, but free in each feature's shape and missingness) and with the
  tuned trees (`lightgbm`, which adds interactions).
- Ladder from inputs shaped like FeatureExtraction's (binary presence in two windows, age groups;
  not its default covariate set) to these features (`--ladder`, `ladder_inputs`): what the extra
  featurization is worth, step by step, with the LR and LightGBM on the same inputs.

All are fit on the training split and predict the held-out split.
"""

import time

import numpy as np
import scipy.sparse as sp

N_TRIALS = 20
SEED = 1
MIN_NONZERO = 100  # training entries a column needs for the tree models
MAX_ROUNDS = 1000
PATIENCE = 50
LR_FOLDS = 3  # cross-validation folds for the LR's prior variance (final runs)
LR_START = 0.01  # where the variance search starts (PLP's default)


def nonzero_counts(X) -> np.ndarray:
    """Rows in which each column is non-zero. Stored zeros (a value aggregation of a measured 0)
    do not count: the models see them as the absent entries they equal."""
    C = sp.csc_matrix(X)
    cumulative = np.concatenate(([0], np.cumsum(C.data != 0)))
    return np.diff(cumulative[C.indptr])


def kept_columns(X) -> np.ndarray:
    """Columns non-zero in at least MIN_NONZERO training rows."""
    return nonzero_counts(X) >= MIN_NONZERO


def redundant_columns(X, codes, aggs, windows) -> np.ndarray:
    """The columns FeatureExtraction's tidyCovariateData (removeRedundancy, as PLP applies it before
    every model) drops, found on the training split: a column with the same non-zero value in every
    row (a copy of the intercept), and, in each group of binary columns that together partition the
    rows (every row in exactly one; for example the two sex indicators), the most prevalent one,
    ties by name. FeatureExtraction groups covariates by analysis; here a group is the columns of
    one code prefix (the code up to its first "/"), aggregation and window. With an intercept these
    are exact linear dependencies: under an L1 prior dropping them leaves the optimum unchanged, and
    kept, they make the face solves of Cyclops' Newton steps singular (2-2.4x slower fits)."""
    from collections import defaultdict

    C = sp.csc_matrix(X)
    n = C.shape[0]
    stored = np.diff(C.indptr)
    filled = np.flatnonzero(stored > 0)
    low = np.zeros(C.shape[1])
    high = np.zeros(C.shape[1])
    low[filled] = np.minimum.reduceat(C.data, C.indptr[filled])
    high[filled] = np.maximum.reduceat(C.data, C.indptr[filled])
    drop = (stored == n) & (low == high) & (low != 0)
    groups = defaultdict(list)
    for j in np.flatnonzero((stored > 0) & (low == 1) & (high == 1) & ~drop):
        groups[(str(codes[j]).split("/")[0], aggs[j], windows[j])].append(j)
    for cols in groups.values():
        if len(cols) < 2 or stored[cols].sum() != n:
            continue
        if np.all(np.asarray(C[:, cols].sum(axis=1)).ravel() == 1):
            drop[min(cols, key=lambda j: (-stored[j], str(codes[j])))] = True
    return drop


def lr_inputs(X, Xh, agg: np.ndarray, kind: str):
    """The LR's columns for one decomposition step: "binary" (each code's count and static
    presence as an indicator) or "counts" (counts and presence, no values)."""
    counts = np.isin(agg, ("count", "present"))
    X, Xh = sp.csr_matrix(X[:, counts]), sp.csr_matrix(Xh[:, counts])
    if kind == "binary":
        X.data[:] = 1
        Xh.data[:] = 1
    return X, Xh


LADDER = ("fe0", "fe", "fe_short", "fe_values", "full_age")
AGE_GROUPS = 20  # 5-year groups, the last 95 and over


def age_groups(age: np.ndarray) -> sp.csr_matrix:
    """FeatureExtraction's demographic age groups: one indicator per 5 years of age (0-4, 5-9,
    ..., 95+); no indicator without an age."""
    known = np.isfinite(age) & (age >= 0)
    rows = np.flatnonzero(known)
    groups = np.minimum(age[known] // 5, AGE_GROUPS - 1).astype(np.int64)
    return sp.csr_matrix((np.ones(len(rows)), (rows, groups)), shape=(len(age), AGE_GROUPS))


def ladder_inputs(mats: list, agg: np.ndarray, window: np.ndarray, step: str, ages: list) -> list:
    """One ladder step's inputs for [train, tuning, held-out] matrices (quintile cuts from the
    first), from FeatureExtraction's default covariates to the full features:
    - fe0: each code's presence in the last 30 days and in all history (the count columns as
      indicators, and static codes' presence): binary covariates in FeatureExtraction's short-term
      and any-time-prior windows (its 180- and 365-day windows and concept roll-ups have no
      counterpart here); sex is a code;
    - fe: and the age groups (FeatureExtraction's demographics);
    - fe_short: and presence in the 1-day and 7-day windows;
    - fe_values: and each value column (sum, min, max, first, in every window) as indicators of its
      training quintile, a stand-in for FeatureExtraction's normal-range groups;
    - full_age: the full features (counts and raw values) and the age groups."""
    counts = np.isin(agg, ("count", "present"))
    if step == "full_age":
        cols = np.ones(len(agg), dtype=bool)
    elif step in ("fe0", "fe"):
        cols = counts & np.isin(window, ("30d", "full", "static"))
    else:
        cols = counts
    out = [sp.csr_matrix(M[:, cols]) for M in mats]
    if step != "full_age":
        for M in out:
            M.data[:] = 1
    if step == "fe_values":
        values = np.isin(agg, ("sum", "min", "max", "first"))
        V = [sp.csc_matrix(M[:, values]) for M in mats]
        every, width = np.ones(V[0].shape[1], dtype=bool), V[0].shape[1]
        bins = [quintiles(V[0], v, every)[1][:, width:] for v in V]
        out = [sp.hstack([o, b], format="csr") for o, b in zip(out, bins, strict=True)]
    if step != "fe0":
        out = [sp.hstack([o, age_groups(a)], format="csr") for o, a in zip(out, ages, strict=True)]
    return out


def quintiles(X, Xh, values: np.ndarray, min_count: int = 10):
    """Each value column also as indicators of its training quintile (of stored entries), so the
    LR gets thresholds and a missing value sets none; indicators with fewer than `min_count`
    training entries are dropped."""
    V, Vh = sp.csc_matrix(X[:, values]), sp.csc_matrix(Xh[:, values])
    cuts = [
        np.quantile(V.data[V.indptr[j] : V.indptr[j + 1]], [0.2, 0.4, 0.6, 0.8])
        if V.indptr[j + 1] > V.indptr[j]
        else np.zeros(4)
        for j in range(V.shape[1])
    ]

    def binned(M):
        rows, cols = [], []
        for j in range(M.shape[1]):
            lo, hi = M.indptr[j], M.indptr[j + 1]
            rows.append(M.indices[lo:hi])
            cols.append(j * 5 + np.searchsorted(cuts[j], M.data[lo:hi], side="right"))
        rows, cols = np.concatenate(rows), np.concatenate(cols)
        return sp.csc_matrix((np.ones(len(rows)), (rows, cols)), shape=(M.shape[0], M.shape[1] * 5))

    B, Bh = binned(V), binned(Vh)
    keep = np.asarray(B.sum(axis=0)).ravel() >= min_count
    return sp.hstack([X, B[:, keep]], format="csc"), sp.hstack([Xh, Bh[:, keep]], format="csc")


def cyclops_lr(
    X, y, Xh, threads: int, values: np.ndarray | None = None, variance: float | None = None
) -> tuple[np.ndarray, str]:
    """Held-out risks of a Cyclops L1 LR; `values` (a column mask) adds the quintile indicators.
    With `variance` the prior variance is frozen (routine runs: one fit); without, it is chosen by
    LR_FOLDS-fold cross-validation, the search starting at LR_START (PLP's default). Control() takes
    the fitter's presets, which for "lr" on Cyclops 4.0 branches are algorithm "auto" with the
    certified "gap" criterion."""
    from cyclops import Control, CyclopsData, CyclopsModel, Prior

    if values is not None:
        X, Xh = quintiles(X, Xh, values)
    scale = np.asarray(abs(X).max(axis=0).todense()).ravel()
    scale[scale == 0] = 1
    D = sp.diags(1 / scale)
    X, Xh = sp.csc_matrix(X @ D), sp.csc_matrix(Xh @ D)
    start = time.time()
    data = CyclopsData.from_arrays(X, np.asarray(y, dtype=float), "lr", add_intercept=True)
    prior = Prior("laplace", variance="cv" if variance is None else float(variance))
    control = Control(threads=threads, seed=SEED, fold=LR_FOLDS, starting_variance=LR_START)
    fit = CyclopsModel(data, prior=prior, control=control).fit()
    coef = np.asarray(fit.coefficients)
    risk = 1 / (1 + np.exp(-(coef[0] + Xh @ coef[1:])))
    report = getattr(fit, "convergence", {}) or {}
    info = (
        f"cyclops: {time.time() - start:.0f} s, {np.count_nonzero(coef[1:])} of {X.shape[1]}"
        f" features, variance {fit.variance} ({'cv' if variance is None else 'frozen'}),"
        f" {fit.return_flag}, {report.get('algorithm', '')} certificate"
        f" {report.get('certificate', 'n/a')}"
    )
    return risk, info


def _lightgbm_train(X, y, Xt, yt, params: dict, threads: int):
    import lightgbm as lgb

    full = {
        "objective": "binary",
        "metric": "auc",
        "num_threads": threads,
        "seed": SEED,
        "deterministic": True,
        "force_col_wise": True,
        "verbose": -1,
        "bagging_freq": 1,
        **params,
    }
    train = lgb.Dataset(X, label=y, params={"verbose": -1, "num_threads": threads})
    tune = lgb.Dataset(Xt, label=yt, reference=train)
    return lgb.train(
        full,
        train,
        num_boost_round=MAX_ROUNDS,
        valid_sets=[tune],
        callbacks=[lgb.early_stopping(PATIENCE, verbose=False)],
    )


def lightgbm_search(X, y, Xt, yt, threads: int, trials: int = N_TRIALS) -> tuple[dict, str]:
    """The hyperparameters of the best of `trials` LightGBM fits, by tuning AUROC."""
    import optuna

    keep = kept_columns(X)
    X, Xt = X[:, keep], Xt[:, keep]
    optuna.logging.set_verbosity(optuna.logging.WARNING)

    def objective(trial):
        params = {
            "learning_rate": trial.suggest_float("learning_rate", 0.02, 0.3, log=True),
            "num_leaves": trial.suggest_int("num_leaves", 15, 511, log=True),
            "min_data_in_leaf": trial.suggest_int("min_data_in_leaf", 5, 500, log=True),
            "feature_fraction": trial.suggest_float("feature_fraction", 0.2, 1.0),
            "bagging_fraction": trial.suggest_float("bagging_fraction", 0.5, 1.0),
            "lambda_l1": trial.suggest_float("lambda_l1", 1e-3, 10, log=True),
            "lambda_l2": trial.suggest_float("lambda_l2", 1e-3, 10, log=True),
        }
        booster = _lightgbm_train(X, y, Xt, yt, params, threads)
        trial.set_user_attr("rounds", booster.best_iteration)
        return booster.best_score["valid_0"]["auc"]

    start = time.time()
    study = optuna.create_study(direction="maximize", sampler=optuna.samplers.TPESampler(seed=SEED))
    study.optimize(objective, n_trials=trials)
    best = study.best_trial
    info = (
        f"lightgbm search: {time.time() - start:.0f} s, {trials} trials, tuning AUROC"
        f" {best.value:.4f}, {best.user_attrs['rounds']} rounds, {keep.sum()} columns"
    )
    return best.params, info


def lightgbm_fit(X, y, Xt, yt, Xh, params: dict, threads: int) -> tuple[np.ndarray, str]:
    """Held-out risks of LightGBM with frozen hyperparameters, stopped early on the tuning split."""
    from sklearn.metrics import roc_auc_score

    keep = kept_columns(X)
    start = time.time()
    booster = _lightgbm_train(X[:, keep], y, Xt[:, keep], yt, params, threads)
    risk = booster.predict(Xh[:, keep], num_iteration=booster.best_iteration)
    tuning = roc_auc_score(yt, booster.predict(Xt[:, keep], num_iteration=booster.best_iteration))
    info = (
        f"lightgbm: {time.time() - start:.0f} s, {booster.best_iteration} rounds, tuning AUROC"
        f" {tuning:.4f}, {keep.sum()} columns, {params}"
    )
    return risk, info


def xgboost_search(X, y, Xt, yt, Xh, threads: int, trials: int = N_TRIALS):
    """Held-out risks of the best of `trials` XGBoost fits (by tuning AUROC), and a summary."""
    import optuna
    import xgboost as xgb
    from sklearn.metrics import roc_auc_score

    keep = kept_columns(X)
    X, Xt, Xh = X[:, keep], Xt[:, keep], Xh[:, keep]
    optuna.logging.set_verbosity(optuna.logging.WARNING)
    dtrain = xgb.QuantileDMatrix(X, label=y, nthread=threads)
    dtune = xgb.QuantileDMatrix(Xt, label=yt, ref=dtrain, nthread=threads)
    best = {"auc": -1.0}

    def objective(trial):
        params = {
            "objective": "binary:logistic",
            "tree_method": "hist",
            "nthread": threads,
            "seed": SEED,
            # a deep tree's histograms over this many columns would otherwise need >150 GB
            "max_cached_hist_node": 64,
            "eta": trial.suggest_float("eta", 1e-3, 1, log=True),
            "lambda": trial.suggest_float("lambda", 1e-3, 1, log=True),
            "alpha": trial.suggest_float("alpha", 1e-3, 1, log=True),
            "subsample": trial.suggest_float("subsample", 0.5, 1),
            "min_child_weight": trial.suggest_float("min_child_weight", 1e-2, 100),
            "max_depth": trial.suggest_int("max_depth", 2, 15),
        }
        rounds = trial.suggest_int("num_boost_round", 100, 999)
        patience = trial.suggest_int("early_stopping_rounds", 1, 9)
        booster = xgb.train(
            params,
            dtrain,
            num_boost_round=rounds,
            evals=[(dtune, "tuning")],
            early_stopping_rounds=patience,
            verbose_eval=False,
        )
        it = (0, booster.best_iteration + 1)
        auc = roc_auc_score(yt, booster.predict(dtune, iteration_range=it))
        if auc > best["auc"]:
            best.update(auc=auc, booster=booster, it=it, params=trial.params)
        return auc

    start = time.time()
    study = optuna.create_study(direction="maximize", sampler=optuna.samplers.TPESampler(seed=SEED))
    study.optimize(objective, n_trials=trials)
    risk = best["booster"].predict(xgb.DMatrix(Xh), iteration_range=best["it"])
    info = (
        f"xgboost: {time.time() - start:.0f} s, {trials} trials, tuning AUROC {best['auc']:.4f},"
        f" {best['it'][1]} trees, {keep.sum()} columns, {best['params']}"
    )
    return risk, info


STUMP_RATE, STUMP_ROUNDS = 0.3, 3000


def lightgbm_stumps(X, y, Xt, yt, Xh, params: dict, threads: int) -> tuple[np.ndarray, str]:
    """Held-out risks of LightGBM with depth-1 trees: the task's tuned regularisation and sampling,
    a higher learning rate (stumps learn little per round), stopped early on the tuning split."""
    import lightgbm as lgb
    from sklearn.metrics import roc_auc_score

    keep = kept_columns(X)
    full = {
        "objective": "binary",
        "metric": "auc",
        "num_threads": threads,
        "seed": SEED,
        "deterministic": True,
        "force_col_wise": True,
        "verbose": -1,
        "bagging_freq": 1,
        **params,
        "num_leaves": 2,
        "max_depth": 1,
        "learning_rate": STUMP_RATE,
    }
    start = time.time()
    train = lgb.Dataset(X[:, keep], label=y, params={"verbose": -1, "num_threads": threads})
    tune = lgb.Dataset(Xt[:, keep], label=yt, reference=train)
    booster = lgb.train(
        full,
        train,
        num_boost_round=STUMP_ROUNDS,
        valid_sets=[tune],
        callbacks=[lgb.early_stopping(PATIENCE, verbose=False)],
    )
    risk = booster.predict(Xh[:, keep], num_iteration=booster.best_iteration)
    tuning = roc_auc_score(yt, booster.predict(Xt[:, keep], num_iteration=booster.best_iteration))
    info = (
        f"lightgbm stumps: {time.time() - start:.0f} s, {booster.best_iteration} rounds, tuning"
        f" AUROC {tuning:.4f}, {keep.sum()} columns"
    )
    return risk, info
