"""Tests for ROI / cluster differential expression extraction (T-011).

Guard discipline: every optional third-party dependency is checked with
``pytest.importorskip`` BEFORE it is imported. ``src/tests/test_clustering.py``
does a bare ``import anndata`` on line 3 and only calls ``importorskip`` on
line 8, so its guards never fire and the module errors at collection instead of
skipping. That mistake is deliberately not repeated here.

No network, no data files, no reliance on the demo dataset (which is not
present in this repository). Every fixture builds a small synthetic AnnData,
and all randomness is explicitly seeded.
"""

from __future__ import annotations

import pytest

pytest.importorskip("numpy")
pytest.importorskip("scipy")
pytest.importorskip("anndata")
pytest.importorskip("shapely")

import json
import os

import anndata as ad
import numpy as np
import scipy.sparse as sp
from scipy.stats import mannwhitneyu, norm, rankdata

from rag.deg import run_roi_deg
from rag.deg.extraction import (
    _rank_high_expression_genes,
    compute_deg,
    get_cluster_high_expression_genes,
    get_roi_high_expression_genes,
)
from rag.deg.filtering import filter_deg_candidates_with_count, gene_detection_counts
from rag.deg.geometry import (
    PolygonValidationError,
    build_roi_mask,
    validate_polygons,
)
from rag.deg.models import MESSAGE_NO_DATA, STATUS_NO_DATA
from rag.deg.stats import (
    REASON_CONSTANT,
    REASON_INSUFFICIENT_SPOTS,
    adjust_pvalues,
    benjamini_hochberg,
    wilcoxon_rank_sum,
)
from rag.deg.workspace import WorkspacePathError, resolve_workspace_file

SEED = 20240725

# Keys the legacy implementation emitted. None may disappear.
LEGACY_RESULT_KEYS = {
    "selected_spots",
    "reference_spots",
    "total_spots",
    "top_genes",
    "ranking_method",
}
LEGACY_GENE_KEYS = {
    "gene",
    "mean_expression",
    "pct_spots_expressed",
    "mean_roi",
    "mean_reference",
    "pct_roi",
    "pct_reference",
    "log2_fold_change",
}


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


def _make_adata(
    matrix: np.ndarray,
    *,
    gene_names: list[str] | None = None,
    spatial: np.ndarray | None = None,
    sparse: bool = False,
) -> ad.AnnData:
    """Build a small AnnData from a dense array."""

    values = np.asarray(matrix, dtype=np.float64)
    payload = sp.csr_matrix(values) if sparse else values
    adata = ad.AnnData(payload)
    adata.var_names = gene_names or [f"GENE_{i}" for i in range(values.shape[1])]
    adata.obs_names = [str(i) for i in range(values.shape[0])]
    if spatial is not None:
        adata.obsm["spatial"] = np.asarray(spatial, dtype=np.float64)
    return adata


@pytest.fixture
def planted_signal() -> tuple[ad.AnnData, np.ndarray]:
    """40 ROI spots vs 40 reference spots; GENE_0 is strongly ROI-enriched."""

    rng = np.random.default_rng(SEED)
    counts = rng.poisson(4.0, size=(80, 30)).astype(np.float64)
    counts[:40, 0] += 60.0
    selected = np.zeros(80, dtype=bool)
    selected[:40] = True
    return _make_adata(counts, sparse=True), selected


@pytest.fixture
def null_dataset() -> tuple[ad.AnnData, np.ndarray]:
    """Pure noise: the ROI label is unrelated to expression."""

    rng = np.random.default_rng(SEED + 1)
    counts = rng.poisson(4.0, size=(80, 40)).astype(np.float64)
    selected = np.zeros(80, dtype=bool)
    selected[:40] = True
    return _make_adata(counts, sparse=True), selected


@pytest.fixture
def workspace(tmp_path) -> dict[str, object]:
    """A minimal on-disk workspace: state json, cluster json, and an h5ad."""

    rng = np.random.default_rng(SEED + 2)
    counts = rng.poisson(3.0, size=(40, 12)).astype(np.float64)
    counts[:20, 0] += 50.0
    spatial = np.column_stack(
        [
            np.concatenate([np.linspace(0, 4, 20), np.linspace(20, 24, 20)]),
            np.zeros(40),
        ]
    )
    adata = _make_adata(counts, spatial=spatial, sparse=True)

    work_dir = tmp_path / "workspace"
    user_dir = work_dir / "user"
    user_dir.mkdir(parents=True)
    h5ad_path = user_dir / "spatial_expression.h5ad"
    adata.write_h5ad(h5ad_path)

    state_path = user_dir / "spatial_omics.json"
    state_path.write_text(json.dumps({"h5ad_path": str(h5ad_path)}), encoding="utf-8")

    cluster_path = user_dir / "spatial_clusters.json"
    cluster_path.write_text(
        json.dumps(
            {
                "cluster_key": "spatial_cluster",
                "clusters": {str(i): ("0" if i < 20 else "1") for i in range(40)},
            }
        ),
        encoding="utf-8",
    )

    return {
        "work_dir": str(work_dir),
        "state_path": str(state_path),
        "cluster_path": str(cluster_path),
        "h5ad_path": str(h5ad_path),
        "state_resolver": lambda _w, _f: str(state_path),
        "cluster_resolver": lambda _w, _f: str(cluster_path),
    }


def _assert_pvalues_are_sane(result_dict: dict) -> None:
    """R5: no NaN, no inf, nothing outside [0, 1] may reach the output."""

    for gene in result_dict["top_genes"]:
        for key in ("pvalue", "adj_pvalue"):
            value = gene[key]
            assert isinstance(value, float)
            assert np.isfinite(value), f"{key} for {gene['gene']} is not finite"
            assert 0.0 <= value <= 1.0, f"{key} for {gene['gene']} out of range"
        assert np.isfinite(gene["log2_fold_change"])
        assert np.isfinite(gene["statistic"])


# ---------------------------------------------------------------------------
# T-008 — Wilcoxon rank-sum
# ---------------------------------------------------------------------------


def test_planted_signal_ranks_first_with_small_adjusted_pvalue(planted_signal):
    adata, selected = planted_signal

    result = compute_deg(adata, selected, top_n=5, min_cells=5, fdr_threshold=0.05)

    assert result.status == "ok"
    assert result.top_genes, "planted signal produced no genes"
    assert result.top_genes[0].gene == "GENE_0"
    assert result.top_genes[0].adj_pvalue < 0.01
    assert result.top_genes[0].log2_fold_change > 1.0
    assert result.fdr_applied is True
    _assert_pvalues_are_sane(result.to_dict())


def test_null_dataset_yields_no_significant_genes(null_dataset):
    adata, selected = null_dataset

    result = compute_deg(adata, selected, top_n=10, min_cells=5, fdr_threshold=0.05)

    assert result.top_genes == []
    assert result.n_significant == 0
    assert result.status == "no_significant_genes"
    assert "no gene passed" in result.status_message.lower()


def test_zero_inflated_gene_produces_sane_pvalue_via_tie_correction():
    # 90% zeros in both groups; ties dominate. Without tie correction the
    # variance is overstated and the p-value drifts toward 1.
    rng = np.random.default_rng(SEED + 3)
    counts = np.zeros((100, 2), dtype=np.float64)
    roi_hits = rng.choice(50, size=25, replace=False)
    ref_hits = rng.choice(50, size=3, replace=False)
    counts[roi_hits, 0] = 5.0
    counts[50 + ref_hits, 0] = 5.0
    selected = np.zeros(100, dtype=bool)
    selected[:50] = True

    statistic, pvalue, testable, reasons = wilcoxon_rank_sum(
        sp.csr_matrix(counts), selected
    )

    assert testable[0]
    assert np.isfinite(pvalue[0])

    # Closed-form check against the tie-corrected normal approximation, with
    # the 0.5 continuity correction. Asserting only "p is small" would NOT
    # guard tie correction: on this input the UNcorrected variance still gives
    # p = 1.5e-04. The corrected value is 1.1e-06, ~136x smaller, so comparing
    # against the exact expected value is what makes this test meaningful.
    values = counts[:, 0]
    n_roi = n_ref = 50
    n_total = 100
    ranks = rankdata(values)
    u_statistic = ranks[selected].sum() - n_roi * (n_roi + 1) / 2
    mu = n_roi * n_ref / 2
    _unique, tie_counts = np.unique(values, return_counts=True)
    tie_term = np.sum(tie_counts**3 - tie_counts)
    sigma_tie = np.sqrt(
        n_roi * n_ref / 12 * ((n_total + 1) - tie_term / (n_total * (n_total - 1)))
    )
    sigma_no_tie = np.sqrt(n_roi * n_ref * (n_total + 1) / 12)
    expected_p = 2 * norm.sf((abs(u_statistic - mu) - 0.5) / sigma_tie)
    uncorrected_p = 2 * norm.sf((abs(u_statistic - mu) - 0.5) / sigma_no_tie)

    assert pvalue[0] == pytest.approx(expected_p, rel=1e-9)
    assert pvalue[0] < uncorrected_p / 10.0, "tie correction is not being applied"
    assert statistic[0] == pytest.approx(u_statistic)

    # The all-zero second gene is constant and must be excluded, not tested.
    assert not testable[1]
    assert reasons[1] == REASON_CONSTANT
    assert pvalue[1] == 1.0


def test_constant_gene_is_untestable_and_never_nan():
    counts = np.ones((20, 3), dtype=np.float64)
    counts[:10, 1] = 7.0
    selected = np.zeros(20, dtype=bool)
    selected[:10] = True

    _statistic, pvalue, testable, reasons = wilcoxon_rank_sum(counts, selected)

    assert not testable[0] and reasons[0] == REASON_CONSTANT
    assert not testable[2] and reasons[2] == REASON_CONSTANT
    assert testable[1]
    assert np.all(np.isfinite(pvalue))
    assert np.all((pvalue >= 0.0) & (pvalue <= 1.0))


@pytest.mark.parametrize("n_selected", [0, 1, 2])
def test_too_few_spots_on_one_side_is_untestable(n_selected):
    counts = np.arange(60, dtype=np.float64).reshape(20, 3)
    selected = np.zeros(20, dtype=bool)
    selected[:n_selected] = True

    _statistic, pvalue, testable, reasons = wilcoxon_rank_sum(counts, selected)

    assert not testable.any()
    assert np.all(pvalue == 1.0)
    assert set(reasons) == {REASON_INSUFFICIENT_SPOTS}


# ---------------------------------------------------------------------------
# Equivalence against scipy — the gate on the hand-rolled rank-sum
# ---------------------------------------------------------------------------
#
# `wilcoxon_rank_sum` is implemented directly rather than by calling
# `scipy.stats.mannwhitneyu(..., axis=0)`, which does not stay vectorised
# through its `_axis_nan_policy` wrapper and dominated the DEG runtime.
#
# These tests are what make that reimplementation defensible: they run BOTH
# implementations over fixed synthetic matrices covering the cases where a
# hand-rolled rank-sum most plausibly diverges from a reference — heavy ties,
# all-zero and constant genes, unequal group sizes, and tiny groups — and
# require agreement to a tight tolerance. They are permanent; do not delete
# them if the implementation changes again.


def _equivalence_matrices() -> dict[str, tuple[np.ndarray, np.ndarray]]:
    """Fixed (matrix, mask) pairs exercising the divergence-prone cases."""

    rng = np.random.default_rng(SEED + 10)
    cases: dict[str, tuple[np.ndarray, np.ndarray]] = {}

    # Heavy ties: ~90% zeros, the dominant real-world regime.
    zero_inflated = np.zeros((80, 25), dtype=np.float64)
    hits = rng.random((80, 25)) < 0.10
    zero_inflated[hits] = rng.integers(1, 6, size=int(hits.sum()))
    mask = np.zeros(80, dtype=bool)
    mask[:40] = True
    cases["zero_inflated_heavy_ties"] = (zero_inflated, mask)

    # Binary data — the most extreme tie structure possible.
    binary = (rng.random((60, 20)) < 0.3).astype(np.float64)
    mask_binary = np.zeros(60, dtype=bool)
    mask_binary[:25] = True
    cases["binary_extreme_ties"] = (binary, mask_binary)

    # Unequal group sizes.
    unequal = rng.poisson(3.0, size=(60, 20)).astype(np.float64)
    mask_unequal = np.zeros(60, dtype=bool)
    mask_unequal[:7] = True
    cases["unequal_group_sizes"] = (unequal, mask_unequal)

    # Continuous values with no ties at all.
    continuous = rng.normal(size=(50, 15))
    mask_continuous = np.zeros(50, dtype=bool)
    mask_continuous[:20] = True
    cases["continuous_no_ties"] = (continuous, mask_continuous)

    # Mixed: all-zero gene, constant nonzero gene, and a real signal together.
    mixed = rng.poisson(2.0, size=(40, 6)).astype(np.float64)
    mixed[:, 0] = 0.0  # all-zero gene
    mixed[:, 1] = 4.0  # constant nonzero gene
    mixed[:20, 2] += 15.0  # planted signal
    mask_mixed = np.zeros(40, dtype=bool)
    mask_mixed[:20] = True
    cases["mixed_degenerate_and_signal"] = (mixed, mask_mixed)

    # Minimum viable group size on one side.
    tiny = rng.poisson(3.0, size=(30, 10)).astype(np.float64)
    mask_tiny = np.zeros(30, dtype=bool)
    mask_tiny[:3] = True
    cases["minimum_group_size"] = (tiny, mask_tiny)

    return cases


@pytest.mark.parametrize("case_name", sorted(_equivalence_matrices()))
def test_hand_rolled_rank_sum_matches_scipy(case_name):
    matrix, mask = _equivalence_matrices()[case_name]

    statistic, pvalue, testable, _reasons = wilcoxon_rank_sum(
        sp.csr_matrix(matrix), mask
    )

    # Reference: scipy, with exactly the conventions documented in stats.py.
    reference = mannwhitneyu(
        matrix[mask],
        matrix[~mask],
        alternative="two-sided",
        use_continuity=True,
        method="asymptotic",
        axis=0,
    )
    ref_p = np.clip(np.asarray(reference.pvalue, dtype=np.float64), 0.0, 1.0)
    ref_u = np.asarray(reference.statistic, dtype=np.float64)

    # Constant genes are deliberately excluded rather than tested: scipy emits
    # NaN for them (zero tie-corrected variance). Compare only where this
    # module actually performed a test.
    compared = testable & np.isfinite(ref_p)
    assert compared.any(), f"{case_name} exercised no testable gene"

    np.testing.assert_allclose(
        pvalue[compared], ref_p[compared], rtol=1e-9, atol=1e-12
    )
    np.testing.assert_allclose(
        statistic[compared], ref_u[compared], rtol=1e-9, atol=1e-12
    )


def test_hand_rolled_rank_sum_matches_scipy_on_single_spot_roi():
    """A 1-spot ROI is refused outright rather than approximated."""

    rng = np.random.default_rng(SEED + 11)
    matrix = rng.poisson(3.0, size=(30, 8)).astype(np.float64)
    mask = np.zeros(30, dtype=bool)
    mask[0] = True

    _statistic, pvalue, testable, reasons = wilcoxon_rank_sum(matrix, mask)

    # Below MIN_SPOTS_PER_GROUP the normal approximation is meaningless, so
    # this module refuses instead of matching scipy, which would still emit a
    # number here. That divergence is intentional.
    assert not testable.any()
    assert np.all(pvalue == 1.0)
    assert set(reasons) == {REASON_INSUFFICIENT_SPOTS}


def test_hand_rolled_rank_sum_agrees_across_chunk_boundaries():
    """Chunking must not change any result."""

    rng = np.random.default_rng(SEED + 12)
    matrix = rng.poisson(2.0, size=(40, 37)).astype(np.float64)
    matrix[:20, 5] += 10.0
    mask = np.zeros(40, dtype=bool)
    mask[:20] = True

    whole = wilcoxon_rank_sum(matrix, mask, chunk_size=1000)
    chunked = wilcoxon_rank_sum(matrix, mask, chunk_size=4)

    np.testing.assert_allclose(whole[0], chunked[0], rtol=1e-12)
    np.testing.assert_allclose(whole[1], chunked[1], rtol=1e-12)
    np.testing.assert_array_equal(whole[2], chunked[2])


def test_hand_rolled_rank_sum_never_emits_invalid_pvalues():
    """R5, re-asserted directly against the rank-sum stage."""

    for _name, (matrix, mask) in _equivalence_matrices().items():
        statistic, pvalue, _testable, _reasons = wilcoxon_rank_sum(
            sp.csr_matrix(matrix), mask
        )
        assert np.all(np.isfinite(pvalue))
        assert np.all(pvalue >= 0.0) and np.all(pvalue <= 1.0)
        assert np.all(np.isfinite(statistic))


# ---------------------------------------------------------------------------
# T-009 — Benjamini-Hochberg
# ---------------------------------------------------------------------------


def test_benjamini_hochberg_matches_hand_computed_vector():
    # p * m / rank = [0.005, 0.02, 0.065, 0.05125, 0.042]; the running minimum
    # from the right pulls 0.065 and 0.05125 down to 0.042.
    pvalues = np.array([0.001, 0.008, 0.039, 0.041, 0.042])

    adjusted = benjamini_hochberg(pvalues)

    expected = np.array([0.005, 0.02, 0.042, 0.042, 0.042])
    np.testing.assert_allclose(adjusted, expected, rtol=1e-12, atol=1e-12)


def test_benjamini_hochberg_is_monotonic_and_clamped():
    rng = np.random.default_rng(SEED + 4)
    pvalues = rng.uniform(0.0, 1.0, size=500)

    adjusted = benjamini_hochberg(pvalues)

    order = np.argsort(pvalues, kind="stable")
    ordered = adjusted[order]
    assert np.all(np.diff(ordered) >= -1e-12), "adjusted p-values must be monotonic"
    assert np.all(adjusted <= 1.0)
    assert np.all(adjusted >= 0.0)


def test_benjamini_hochberg_honours_explicit_denominator():
    pvalues = np.array([0.01, 0.02])

    adjusted = benjamini_hochberg(pvalues, n_tests=10)

    np.testing.assert_allclose(adjusted, np.array([0.1, 0.1]), rtol=1e-12)


def test_untestable_genes_are_excluded_from_the_denominator():
    pvalues = np.array([0.01, 0.02, 1.0, 1.0])
    testable = np.array([True, True, False, False])

    adjusted, n_tested = adjust_pvalues(pvalues, testable)

    assert n_tested == 2, "BH denominator must count only tested genes"
    # Corrected over 2, not 4: 0.01*2/1 = 0.02, 0.02*2/2 = 0.02.
    np.testing.assert_allclose(adjusted[:2], np.array([0.02, 0.02]), rtol=1e-12)
    assert adjusted[2] == 1.0 and adjusted[3] == 1.0


def test_benjamini_hochberg_handles_empty_input():
    assert benjamini_hochberg(np.array([])).size == 0
    adjusted, n_tested = adjust_pvalues(np.array([]), np.array([], dtype=bool))
    assert n_tested == 0 and adjusted.size == 0


# ---------------------------------------------------------------------------
# T-010 — candidate pre-filtering
# ---------------------------------------------------------------------------


def test_detection_counts_match_between_sparse_and_dense():
    dense = np.array(
        [[0.0, 1.0, 0.0], [2.0, 0.0, 0.0], [3.0, 4.0, 0.0], [0.0, 0.0, 0.0]]
    )

    dense_counts = gene_detection_counts(dense)
    sparse_counts = gene_detection_counts(sp.csr_matrix(dense))

    np.testing.assert_array_equal(dense_counts, np.array([2, 2, 0]))
    np.testing.assert_array_equal(sparse_counts, np.array([2, 2, 0]))


def test_detection_counts_ignore_explicitly_stored_zeros():
    # An explicit stored zero would be miscounted by a naive getnnz().
    matrix = sp.csr_matrix((4, 2), dtype=np.float64).tolil()
    matrix[0, 0] = 0.0
    matrix[1, 0] = 5.0
    csr = matrix.tocsr()

    counts = gene_detection_counts(csr)

    assert counts[0] == 1, "an explicit zero is not a detection"


def test_prefilter_removes_low_detection_genes_and_leaves_input_untouched():
    dense = np.zeros((20, 4), dtype=np.float64)
    dense[:, 0] = 1.0  # detected in 20 spots
    dense[:12, 1] = 1.0  # detected in 12 spots
    dense[:3, 2] = 1.0  # detected in 3 spots
    # column 3 never detected
    adata = _make_adata(dense, sparse=True)
    original_shape = adata.shape

    filtered, removed = filter_deg_candidates_with_count(adata, min_cells=10)

    assert removed == 2
    assert list(filtered.var_names) == ["GENE_0", "GENE_1"]
    assert adata.shape == original_shape, "caller's AnnData must not be mutated"
    assert sp.issparse(filtered.X), "pre-filter must not densify"


def test_prefilter_shrinks_the_bh_denominator():
    dense = np.zeros((30, 6), dtype=np.float64)
    rng = np.random.default_rng(SEED + 5)
    dense[:, :2] = rng.poisson(5.0, size=(30, 2)) + 1.0
    dense[:2, 2] = 4.0  # only 2 detections — will be filtered at min_cells=10
    dense[:1, 3] = 4.0
    selected = np.zeros(30, dtype=bool)
    selected[:15] = True
    adata = _make_adata(dense, sparse=True)

    unfiltered = compute_deg(adata, selected, top_n=10, min_cells=0)
    filtered = compute_deg(adata, selected, top_n=10, min_cells=10)

    assert filtered.n_genes_filtered_out == 4
    assert filtered.n_genes_input == 6
    assert filtered.n_genes_tested < unfiltered.n_genes_tested
    assert filtered.n_genes_tested == 2
    assert filtered.min_cells == 10


def test_prefilter_disabled_keeps_every_gene():
    dense = np.zeros((10, 3), dtype=np.float64)
    dense[0, 0] = 1.0
    adata = _make_adata(dense)

    filtered, removed = filter_deg_candidates_with_count(adata, min_cells=0)

    assert removed == 0
    assert filtered.n_vars == 3


# ---------------------------------------------------------------------------
# Degenerate selections
# ---------------------------------------------------------------------------


def test_empty_roi_returns_empty_selection_status(planted_signal):
    adata, _selected = planted_signal

    result = compute_deg(adata, np.zeros(adata.n_obs, dtype=bool), top_n=5)

    assert result.top_genes == []
    assert result.status == "empty_selection"
    assert result.selected_spots == 0
    assert result.reference_spots == adata.n_obs


def test_roi_covering_every_spot_has_no_reference(planted_signal):
    adata, _selected = planted_signal

    result = compute_deg(adata, np.ones(adata.n_obs, dtype=bool), top_n=5)

    assert result.reference_spots == 0
    assert result.ranking_method.endswith("_mean_expression_only_no_reference")
    assert result.n_genes_tested == 0, "no reference spots means nothing is testable"
    _assert_pvalues_are_sane(result.to_dict())


def test_single_spot_roi_is_safe_and_untestable(planted_signal):
    adata, _selected = planted_signal
    selected = np.zeros(adata.n_obs, dtype=bool)
    selected[0] = True

    result = compute_deg(adata, selected, top_n=5)

    assert result.selected_spots == 1
    assert result.n_genes_tested == 0
    payload = result.to_dict()
    _assert_pvalues_are_sane(payload)
    for gene in payload["top_genes"]:
        assert gene["adj_pvalue"] == 1.0
        assert gene["testable"] is False


def test_empty_gene_list_returns_no_data_status():
    adata = ad.AnnData(np.zeros((6, 0), dtype=np.float64))
    selected = np.zeros(6, dtype=bool)
    selected[:3] = True

    result = compute_deg(adata, selected, top_n=5)

    assert result.top_genes == []
    assert result.status == STATUS_NO_DATA
    assert result.status_message == MESSAGE_NO_DATA


# ---------------------------------------------------------------------------
# T-044 — missing / corrupt data
# ---------------------------------------------------------------------------


def test_missing_h5ad_reports_no_gene_expression_data_loaded(tmp_path):
    result = run_roi_deg(str(tmp_path / "absent.h5ad"), np.array([True, False]))

    assert result.top_genes == []
    assert result.status == STATUS_NO_DATA
    assert result.status_message == MESSAGE_NO_DATA


def test_corrupt_h5ad_reports_no_gene_expression_data_loaded(tmp_path):
    corrupt = tmp_path / "corrupt.h5ad"
    corrupt.write_bytes(b"this is definitely not an HDF5 container")

    result = run_roi_deg(str(corrupt), np.array([True, False]))

    assert result.status == STATUS_NO_DATA
    assert result.status_message == MESSAGE_NO_DATA


def test_empty_path_reports_no_gene_expression_data_loaded():
    assert run_roi_deg("", None).status_message == MESSAGE_NO_DATA
    assert run_roi_deg("   ", None).status_message == MESSAGE_NO_DATA


def test_no_data_message_is_distinct_from_no_significant_genes(null_dataset):
    adata, selected = null_dataset

    ran_but_found_nothing = compute_deg(
        adata, selected, top_n=5, min_cells=5, fdr_threshold=0.05
    )

    assert ran_but_found_nothing.status_message != MESSAGE_NO_DATA
    assert ran_but_found_nothing.status != STATUS_NO_DATA


def test_no_genes_are_ever_fabricated(workspace):
    """T-044: an empty result must stay empty, never padded with demo genes."""

    result = run_roi_deg(str(workspace["h5ad_path"]), np.zeros(40, dtype=bool))

    assert result.top_genes == []
    assert result.to_dict()["top_genes"] == []


# ---------------------------------------------------------------------------
# Polygon validation
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "coords",
    [
        None,
        [],
        [[]],
        [[[0.0, 0.0], [1.0, 1.0]]],  # too few vertices
        [[[0.0, 0.0], [1.0, 0.0], [float("nan"), 1.0]]],
        [[[0.0, 0.0], [1.0, 0.0], [float("inf"), 1.0]]],
        [[[0.0, 0.0], [1.0, 0.0], ["a", "b"]]],
        [[[0.0, 0.0], [1.0, 0.0], [1.0]]],
        [[[0.0, 0.0], [1.0, 0.0], [1e300, 1.0]]],
        "not-a-polygon",
        [[[0.0, 0.0], [2.0, 2.0], [2.0, 0.0], [0.0, 2.0]]],  # self-intersecting
        [[[0.0, 0.0], [1.0, 1.0], [2.0, 2.0]]],  # zero area
    ],
)
def test_malformed_polygons_are_rejected_cleanly(coords):
    with pytest.raises(PolygonValidationError):
        validate_polygons(coords)


def test_valid_polygon_selects_covered_spots_inclusive_of_the_boundary():
    polygons = validate_polygons([[[0.0, 0.0], [2.0, 0.0], [2.0, 2.0], [0.0, 2.0]]])
    spatial = np.array([[1.0, 1.0], [0.0, 0.0], [5.0, 5.0], [np.nan, 1.0]])

    mask = build_roi_mask(spatial, polygons)

    # Interior and boundary are both inside, matching Polygon.covers semantics.
    np.testing.assert_array_equal(mask, np.array([True, True, False, False]))


def test_multiple_polygons_union_their_selections():
    polygons = validate_polygons(
        [
            [[0.0, 0.0], [1.0, 0.0], [1.0, 1.0], [0.0, 1.0]],
            [[10.0, 10.0], [11.0, 10.0], [11.0, 11.0], [10.0, 11.0]],
        ]
    )
    spatial = np.array([[0.5, 0.5], [10.5, 10.5], [5.0, 5.0]])

    mask = build_roi_mask(spatial, polygons)

    np.testing.assert_array_equal(mask, np.array([True, True, False]))


def test_roi_selection_rejects_bad_polygons_without_raising(workspace):
    result = run_roi_deg(
        str(workspace["h5ad_path"]),
        [[[0.0, 0.0], [1.0, 1.0]]],
    )

    assert result.status == "invalid_input"
    assert result.top_genes == []


# ---------------------------------------------------------------------------
# Path traversal
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "folder_id",
    ["..", "../..", "../../etc", "/abs", "a/b", "a\\b", "C:", "id\x00", "a b", "!"],
)
def test_path_traversal_in_folder_id_is_rejected(folder_id):
    with pytest.raises(WorkspacePathError):
        resolve_workspace_file("/tmp/work", folder_id, lambda w, f: f"{w}/user{f}/x")


@pytest.mark.parametrize("folder_id", ["", "abc", "user-1", "A_9"])
def test_safe_folder_ids_are_accepted(tmp_path, folder_id):
    resolved = resolve_workspace_file(
        str(tmp_path),
        folder_id,
        lambda w, f: os.path.join(w, f"user{f}", "state.json"),
    )

    assert str(tmp_path) in resolved


def test_resolver_escaping_the_workspace_is_rejected(tmp_path):
    with pytest.raises(WorkspacePathError):
        resolve_workspace_file(
            str(tmp_path),
            "",
            lambda w, _f: os.path.join(w, "..", "..", "escaped.json"),
        )


def test_traversal_attempt_returns_none_from_the_public_wrapper(workspace):
    result = get_roi_high_expression_genes(
        workspace["work_dir"],
        [[[0.0, 0.0], [5.0, 0.0], [5.0, 1.0], [0.0, 1.0]]],
        folder_id="../../etc",
    )

    assert result is None


# ---------------------------------------------------------------------------
# Backward compatibility (R6)
# ---------------------------------------------------------------------------


def test_rank_high_expression_genes_orders_positive_fold_change_genes():
    adata = _make_adata(
        [
            [10.0, 1.0, 0.0],
            [8.0, 1.0, 0.0],
            [1.0, 9.0, 2.0],
            [1.0, 8.0, 2.0],
        ],
        gene_names=["GENE_A", "GENE_B", "GENE_C"],
    )
    selected = np.array([True, True, False, False])

    result = _rank_high_expression_genes(
        adata,
        selected,
        top_n=2,
        ranking_label="roi_vs_non_roi_log2fc",
    )

    assert result["selected_spots"] == 2
    assert result["reference_spots"] == 2
    assert result["ranking_method"] == "roi_vs_non_roi_log2fc"
    assert [gene["gene"] for gene in result["top_genes"]] == ["GENE_A"]
    assert result["top_genes"][0]["log2_fold_change"] > 0


def test_rank_high_expression_genes_empty_selection_returns_no_genes():
    adata = _make_adata(np.ones((3, 2)), gene_names=["GENE_A", "GENE_B"])

    result = _rank_high_expression_genes(
        adata,
        np.array([False, False, False]),
        top_n=5,
        ranking_label="roi_vs_non_roi_log2fc",
    )

    assert result["selected_spots"] == 0
    assert result["reference_spots"] == 3
    assert result["top_genes"] == []


def test_legacy_values_are_locked_bit_for_bit():
    """R6: today's numbers must not move under the new implementation."""

    adata = _make_adata(
        [
            [10.0, 1.0, 0.0],
            [8.0, 1.0, 0.0],
            [1.0, 9.0, 2.0],
            [1.0, 8.0, 2.0],
        ],
        gene_names=["GENE_A", "GENE_B", "GENE_C"],
    )
    selected = np.array([True, True, False, False])

    result = _rank_high_expression_genes(
        adata, selected, top_n=2, ranking_label="roi_vs_non_roi_log2fc"
    )
    gene = result["top_genes"][0]

    assert LEGACY_RESULT_KEYS <= result.keys()
    assert LEGACY_GENE_KEYS <= gene.keys()
    assert result["total_spots"] == 4
    assert gene["gene"] == "GENE_A"
    assert gene["mean_expression"] == pytest.approx(9.0)
    assert gene["mean_reference"] == pytest.approx(1.0)
    assert gene["pct_spots_expressed"] == pytest.approx(1.0)
    assert gene["pct_reference"] == pytest.approx(1.0)
    # log2(9 / 1) with the original 1e-9 pseudocount.
    assert gene["log2_fold_change"] == pytest.approx(np.log2(9.0), rel=1e-9)
    # Redundant legacy aliases must still mirror their canonical fields.
    assert gene["mean_roi"] == gene["mean_expression"]
    assert gene["pct_roi"] == gene["pct_spots_expressed"]


def test_legacy_wrapper_does_not_filter_by_fdr_and_says_so(workspace):
    coords = [[[-1.0, -1.0], [5.0, -1.0], [5.0, 1.0], [-1.0, 1.0]]]

    result = get_roi_high_expression_genes(
        workspace["work_dir"],
        coords,
        state_path_resolver=workspace["state_resolver"],
    )

    assert result is not None
    assert LEGACY_RESULT_KEYS <= result.keys()
    assert result["fdr_applied"] is False
    assert result["fdr_threshold"] is None
    assert result["top_genes"], "legacy ranking must still return genes"
    assert LEGACY_GENE_KEYS <= result["top_genes"][0].keys()
    _assert_pvalues_are_sane(result)


def test_legacy_roi_wrapper_still_returns_none_without_coords(workspace):
    assert (
        get_roi_high_expression_genes(
            workspace["work_dir"],
            [],
            state_path_resolver=workspace["state_resolver"],
        )
        is None
    )


def test_legacy_roi_wrapper_returns_none_when_state_is_missing(tmp_path):
    assert (
        get_roi_high_expression_genes(
            str(tmp_path),
            [[[0.0, 0.0], [1.0, 0.0], [1.0, 1.0]]],
            state_path_resolver=lambda w, _f: os.path.join(w, "missing.json"),
        )
        is None
    )


def test_cluster_wrapper_keeps_cluster_id_and_cluster_key(workspace):
    result = get_cluster_high_expression_genes(
        workspace["work_dir"],
        "0",
        top_n=5,
        state_path_resolver=workspace["state_resolver"],
        cluster_path_resolver=workspace["cluster_resolver"],
    )

    assert result is not None
    assert LEGACY_RESULT_KEYS <= result.keys()
    assert result["cluster_id"] == "0"
    assert result["cluster_key"] == "spatial_cluster"
    assert result["selected_spots"] == 20
    assert result["reference_spots"] == 20
    assert result["top_genes"][0]["gene"] == "GENE_0"
    _assert_pvalues_are_sane(result)


def test_cluster_wrapper_returns_none_for_missing_cluster_id(workspace):
    assert (
        get_cluster_high_expression_genes(
            workspace["work_dir"],
            None,
            state_path_resolver=workspace["state_resolver"],
            cluster_path_resolver=workspace["cluster_resolver"],
        )
        is None
    )


def test_unknown_cluster_id_produces_empty_selection(workspace):
    result = get_cluster_high_expression_genes(
        workspace["work_dir"],
        "does-not-exist",
        state_path_resolver=workspace["state_resolver"],
        cluster_path_resolver=workspace["cluster_resolver"],
    )

    assert result is not None
    assert result["selected_spots"] == 0
    assert result["top_genes"] == []


# ---------------------------------------------------------------------------
# run_roi_deg end to end
# ---------------------------------------------------------------------------


def test_run_roi_deg_applies_fdr_by_default(workspace):
    coords = [[[-1.0, -1.0], [5.0, -1.0], [5.0, 1.0], [-1.0, 1.0]]]

    result = run_roi_deg(str(workspace["h5ad_path"]), coords)

    assert result.fdr_applied is True
    assert result.fdr_threshold == pytest.approx(0.05)
    assert result.selected_spots == 20
    assert result.top_genes[0].gene == "GENE_0"
    assert result.n_genes_tested > 0
    assert "fdr" in result.ranking_method


def test_run_roi_deg_accepts_a_boolean_mask(workspace):
    mask = np.zeros(40, dtype=bool)
    mask[:20] = True

    result = run_roi_deg(str(workspace["h5ad_path"]), mask)

    assert result.selected_spots == 20
    assert result.top_genes[0].gene == "GENE_0"


def test_run_roi_deg_rejects_a_wrong_length_mask(workspace):
    result = run_roi_deg(str(workspace["h5ad_path"]), np.zeros(7, dtype=bool))

    assert result.status == "invalid_input"
    assert result.top_genes == []


def test_run_roi_deg_threshold_override_is_honoured(workspace):
    mask = np.zeros(40, dtype=bool)
    mask[:20] = True

    strict = run_roi_deg(
        str(workspace["h5ad_path"]), mask, {"fdr_threshold": 1e-12}
    )

    assert strict.fdr_threshold == pytest.approx(1e-12)
    assert strict.top_genes == []
    assert strict.status == "no_significant_genes"


def test_expression_source_reports_observed_integrality(workspace):
    mask = np.zeros(40, dtype=bool)
    mask[:20] = True

    integral = run_roi_deg(str(workspace["h5ad_path"]), mask)
    assert integral.expression_source == "raw_counts_unnormalized"

    fractional = compute_deg(
        _make_adata(np.full((10, 2), 0.5), sparse=True),
        np.array([True] * 5 + [False] * 5),
    )
    assert fractional.expression_source == "non_integer_values_provenance_unknown"


def test_result_dict_is_json_serialisable(workspace):
    mask = np.zeros(40, dtype=bool)
    mask[:20] = True

    payload = run_roi_deg(str(workspace["h5ad_path"]), mask).to_dict()

    # Guards against numpy scalars leaking into session JSON.
    json.dumps(payload)
