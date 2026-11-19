import sys
from pathlib import Path

import numpy as np
import scipy.sparse as sp

sys.path.insert(0, str(Path(__file__).parents[1]))
import models  # noqa: E402


def test_stored_zeros_do_not_count_as_nonzero():
    # column 0: three stored entries, two of them zeros; column 1 empty; column 2 two non-zeros
    data, rows, starts = (
        np.array([0.0, 5.0, 0.0, 1.0, 2.0]),
        np.array([0, 1, 2, 0, 3]),
        [0, 3, 3, 5],
    )
    X = sp.csc_matrix((data, rows, np.array(starts)), shape=(4, 3))
    assert list(models.nonzero_counts(X)) == [1, 0, 2]


def test_redundant_columns_follow_feature_extraction():
    n = 6
    female = np.array([1, 1, 1, 1, 0, 0.0])
    columns = {
        # (code, agg, window): column
        ("MEDS_BIRTH", "count", "full"): np.ones(n),  # in every row: the intercept
        ("CONST", "max", "full"): np.full(n, 2.5),  # same value everywhere
        ("GENDER//F", "present", "static"): female,  # sex partitions; F most common
        ("GENDER//M", "present", "static"): 1 - female,
        ("Gender/F", "count", "full"): female,  # the same, as birth events
        ("Gender/M", "count", "full"): 1 - female,
        ("Gender/F", "count", "1d"): np.array([1, 0, 0, 0, 0, 0.0]),  # other windows: not grouped
        ("RACE//A", "present", "static"): np.array([1, 1, 0, 0, 0, 0.0]),  # misses a row: kept
        ("RACE//B", "present", "static"): np.array([0, 0, 1, 1, 0, 0.0]),
        ("RACE//C", "present", "static"): np.array([0, 0, 0, 0, 1, 0.0]),
        ("LAB//1", "count", "1d"): np.array([1, 1, 1, 0, 0, 0.0]),  # sums to n but overlaps: kept
        ("LAB//2", "count", "1d"): np.array([1, 1, 0, 1, 0, 0.0]),
        ("LAB//3", "count", "1d"): np.array([0, 0, 0, 0, 0, 0.0]),
        ("NA", "count", "full"): np.array([2, 1, 1, 1, 1, 1.0]),  # in every row, not constant
    }
    keys = list(columns)
    X = sp.csr_matrix(np.column_stack([columns[k] for k in keys]))
    codes, aggs, windows = (np.array([k[i] for k in keys]) for i in range(3))
    drop = models.redundant_columns(X, codes, aggs, windows)
    dropped = {keys[j] for j in np.flatnonzero(drop)}
    assert dropped == {
        ("MEDS_BIRTH", "count", "full"),
        ("CONST", "max", "full"),
        ("GENDER//F", "present", "static"),
        ("Gender/F", "count", "full"),
    }


def test_ties_drop_the_first_name():
    half = np.array([1, 1, 0, 0.0])
    X = sp.csr_matrix(np.column_stack([1 - half, half]))
    drop = models.redundant_columns(X, np.array(["S//B", "S//A"]), np.array(["present"] * 2),
                                    np.array(["static"] * 2))  # fmt: skip
    assert list(drop) == [False, True]


def test_age_groups_are_five_year_indicators():
    A = models.age_groups(np.array([0.0, 4.99, 5.0, 67.3, 120.0, np.nan]))
    assert A.shape == (6, models.AGE_GROUPS)
    assert [list(np.flatnonzero(r)) for r in A.toarray()] == [[0], [0], [1], [13], [19], []]


def test_ladder_steps_select_featureextraction_like_inputs():
    # columns: count in 1d, 30d, full; a value (min) in 30d; a static code's presence
    agg = np.array(["count", "count", "count", "min", "present"])
    window = np.array(["1d", "30d", "full", "30d", "static"])
    rng = np.random.default_rng(0)
    n = 60  # enough entries for every quintile indicator to be kept (models.quintiles min_count)
    dense = np.column_stack(
        [rng.integers(0, 3, (n, 3)), np.arange(1.0, n + 1), rng.integers(0, 2, n)]
    )
    X = sp.csr_matrix(dense)
    ages = [rng.uniform(0, 100, n)] * 3
    ages[0][1] = 71.0
    step = {s: models.ladder_inputs([X, X, X], agg, window, s, ages) for s in models.LADDER}
    fe0 = step["fe0"][0].toarray()
    assert (fe0 == (dense[:, [1, 2, 4]] > 0)).all()  # 30d, full and static presence, as 0/1
    fe = step["fe"][0].toarray()
    assert fe.shape == (n, 3 + models.AGE_GROUPS) and fe[1, 3 + 14] == 1  # 71 -> 70-74
    assert step["fe_short"][0].shape == (n, 4 + models.AGE_GROUPS)  # and the 1-day count
    bins = step["fe_values"][0][:, 4 : 4 + 5].toarray()  # the value's five quintile indicators
    assert (bins.sum(axis=1) == 1).all() and (bins.sum(axis=0) == n // 5).all()
    assert step["full_age"][0].shape == (n, 5 + models.AGE_GROUPS)
    assert step["full_age"][0][7, 3] == 8.0  # raw values kept
