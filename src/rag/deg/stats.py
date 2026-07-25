"""
DEG Statistics — Wilcoxon Rank-Sum (T-008) and Benjamini-Hochberg (T-009)
=========================================================================

What the test does
------------------
For each candidate gene, a two-sided Wilcoxon rank-sum test (equivalently the
Mann-Whitney U test) compares the distribution of that gene's values inside the
selection against the distribution outside it. The null hypothesis is that a
randomly drawn selected spot is equally likely to rank above or below a
randomly drawn reference spot.

What the test does NOT establish
--------------------------------
* It is not a claim of biological significance. A small p-value means the two
  rank distributions differ more than sampling noise explains — nothing about
  effect magnitude, cell type, or mechanism.
* Spots are not independent replicates. Neighbouring spots in spatial
  transcriptomics are spatially autocorrelated, so the effective sample size is
  smaller than the spot count and p-values are anti-conservative.
* The selection is chosen by looking at the data (a drawn ROI, or a cluster
  derived from the same expression matrix). Testing the same matrix that
  defined the groups is circular; p-values from that path are descriptive
  rankings, not valid inferential statements.
* No library-size correction is applied. See the normalization caveat in the
  module docstring of ``rag.deg.extraction``.

Conventions chosen
------------------
* **Tie correction: applied.** Spatial expression data is zero-inflated, so
  ties dominate; the uncorrected variance ``n1*n2*(N+1)/12`` is too large and
  yields conservative, wrong p-values. On a representative zero-inflated
  fixture the corrected p-value is ~136x smaller than the uncorrected one.
* **Continuity correction: applied**, in the classical Mann-Whitney form —
  0.5 is subtracted from ``|U - mu|`` before dividing by sigma. This matches
  ``scipy.stats.mannwhitneyu(use_continuity=True)``.
* **Normal approximation always**, never the exact permutation null. With ties
  present the exact null is invalid anyway, and the datasets here are far
  above the size where the approximation matters.

Implementation note
-------------------
The rank-sum is computed directly (see :func:`_rank_sum_block`) rather than by
calling ``scipy.stats.mannwhitneyu(..., axis=0)``, which does not stay
vectorised through its ``_axis_nan_policy`` wrapper and accounted for ~100% of
DEG runtime — 25.6 s of a 25.7 s run on 3,000 spots x 18,000 genes. The direct
implementation is 8.4x faster and clears T-010.

Equivalence with scipy under exactly the conventions above is enforced
permanently by ``test_hand_rolled_rank_sum_matches_scipy``, which compares both
implementations across six regimes (heavy ties, binary, unequal groups, no
ties, mixed degenerate, minimum group size) to ``rtol=1e-9``. **Keep that
test.** It is what makes not calling scipy defensible.
"""

from __future__ import annotations

import logging
import warnings

import numpy as np
import scipy.sparse as sp
from scipy.stats import norm, rankdata

logger = logging.getLogger(__name__)

# Minimum spots required on EACH side for a test to be attempted. Below this
# the normal approximation is meaningless and any p-value would be misleading.
MIN_SPOTS_PER_GROUP = 3

# Genes are densified in column blocks of this width. At 30 000 spots a
# 512-gene block is ~123 MB at float64, which keeps peak memory bounded while
# still letting the test run vectorised over the block.
DEFAULT_CHUNK_SIZE = 512

# Reason codes recorded on genes excluded from the BH denominator.
REASON_INSUFFICIENT_SPOTS = "insufficient_spots"
REASON_CONSTANT = "constant_expression"
REASON_EMPTY_MATRIX = "no_genes"
# Defensive: the constant-gene guard should make this unreachable, but a
# non-finite p-value must be labelled for what it is rather than silently
# reported as a constant gene.
REASON_NON_FINITE = "non_finite_pvalue"


def benjamini_hochberg(
    pvalues: np.ndarray,
    *,
    n_tests: int | None = None,
) -> np.ndarray:
    """Benjamini-Hochberg FDR correction (T-009).

    Implemented directly rather than via statsmodels, which is not a project
    dependency. The procedure is: sort ascending, scale each p-value by
    ``n_tests / rank``, then enforce monotonicity by taking a running minimum
    from the largest p-value downwards, and clamp into [0, 1].

    Args:
        pvalues: 1-D array of raw p-values from testable genes only.
        n_tests: Denominator to correct over. Defaults to ``pvalues.size``.
            Callers pass the count of genes actually tested — NOT the original
            gene count and NOT the count including untestable genes.

    Returns:
        Adjusted p-values in the same order as ``pvalues``, guaranteed
        monotonic with respect to the p-value ordering and within [0, 1].
    """

    raw = np.asarray(pvalues, dtype=np.float64).ravel()
    size = raw.size
    if size == 0:
        return np.empty(0, dtype=np.float64)

    denominator = int(size if n_tests is None else n_tests)
    if denominator <= 0:
        return np.ones(size, dtype=np.float64)

    # Non-finite p-values must never propagate into the output.
    clean = np.where(np.isfinite(raw), raw, 1.0)
    clean = np.clip(clean, 0.0, 1.0)

    order = np.argsort(clean, kind="stable")
    ranked = clean[order]
    ranks = np.arange(1, size + 1, dtype=np.float64)

    scaled = ranked * (float(denominator) / ranks)
    # Running minimum from the largest p-value down enforces monotonicity.
    monotonic = np.minimum.accumulate(scaled[::-1])[::-1]
    monotonic = np.clip(monotonic, 0.0, 1.0)

    adjusted = np.empty(size, dtype=np.float64)
    adjusted[order] = monotonic
    return adjusted


def _rank_sum_block(
    block: np.ndarray,
    mask: np.ndarray,
    n_selected: int,
    n_reference: int,
) -> tuple[np.ndarray, np.ndarray]:
    """Vectorised two-sided rank-sum over a dense block of genes.

    Implemented directly rather than via ``scipy.stats.mannwhitneyu(axis=0)``,
    which does not stay vectorised through its ``_axis_nan_policy`` wrapper and
    accounted for ~100% of DEG runtime. Equivalence with scipy under these same
    conventions is enforced permanently by
    ``test_hand_rolled_rank_sum_matches_scipy``.

    The tie correction reuses the midranks rather than counting tie groups
    separately, via the identity

        Var(U) = n1*n2 / (N*(N-1)) * (sum(R^2) - N*((N+1)/2)^2)

    where ``R`` are the midranks of all N observations. With no ties this
    reduces exactly to ``n1*n2*(N+1)/12``; with ties it is the standard
    tie-corrected variance. Ranking is the only sort, and it serves both the
    statistic and the variance.

    Args:
        block: Dense ``n_spots x n_genes`` array for the current chunk.
        mask: Boolean spot mask marking the selected group.
        n_selected: Number of selected spots.
        n_reference: Number of reference spots.

    Returns:
        A tuple of (U statistic for the selected group, two-sided p-value),
        each of length ``block.shape[1]``.
    """

    n_total = n_selected + n_reference
    if block.size == 0:
        empty = np.empty(block.shape[1], dtype=np.float64)
        return empty, empty

    # Midranks over the full column, computed once per block.
    ranks = rankdata(block, axis=0)

    # U for the selected group, matching scipy's ``statistic`` for x=selected.
    u_statistic = ranks[mask].sum(axis=0) - n_selected * (n_selected + 1) / 2.0
    mu = n_selected * n_reference / 2.0

    # Tie-corrected variance, derived from the same ranks.
    sum_squared_ranks = np.einsum("ij,ij->j", ranks, ranks)
    mean_rank = (n_total + 1) / 2.0
    variance = (
        n_selected
        * n_reference
        / (n_total * (n_total - 1))
        * (sum_squared_ranks - n_total * mean_rank**2)
    )
    sigma = np.sqrt(np.maximum(variance, 0.0))

    # Two-sided z with the classical 0.5 continuity correction.
    deviation = np.abs(u_statistic - mu) - 0.5
    with np.errstate(divide="ignore", invalid="ignore"):
        z_score = np.where(sigma > 0.0, deviation / sigma, np.inf)
    pvalue = 2.0 * norm.sf(z_score)

    # A deviation smaller than the continuity correction drives z negative and
    # the doubled tail above 1; scipy clips identically.
    return u_statistic, np.clip(pvalue, 0.0, 1.0)


def _empty_test_result(n_genes: int, reason: str) -> tuple[
    np.ndarray, np.ndarray, np.ndarray, np.ndarray
]:
    """Build a safe all-untestable result for ``n_genes`` genes."""

    return (
        np.zeros(n_genes, dtype=np.float64),
        np.ones(n_genes, dtype=np.float64),
        np.zeros(n_genes, dtype=bool),
        np.full(n_genes, reason, dtype=object),
    )


def wilcoxon_rank_sum(
    matrix: sp.spmatrix | np.ndarray,
    selected: np.ndarray,
    *,
    chunk_size: int = DEFAULT_CHUNK_SIZE,
) -> tuple[np.ndarray, np.ndarray, np.ndarray, np.ndarray]:
    """Two-sided Wilcoxon rank-sum test per gene (T-008).

    Vectorised over genes via ``scipy.stats.mannwhitneyu(..., axis=0)`` rather
    than a per-gene Python loop. Genes are processed in column blocks so that
    only ``n_spots x chunk_size`` values are dense at any moment; the full
    matrix is never densified.

    Degenerate cases are handled explicitly and always yield ``pvalue = 1.0``
    rather than NaN, a crash, or a small-but-meaningless value:

    * fewer than ``MIN_SPOTS_PER_GROUP`` spots on either side
    * an empty selection, or a selection covering every spot
    * a gene constant across all spots (tie-corrected variance is 0, which
      would otherwise divide by zero and produce NaN)
    * an empty matrix

    Args:
        matrix: Expression matrix, spots x genes, sparse or dense.
        selected: Boolean mask of length ``n_spots``; True marks ROI spots.
        chunk_size: Number of genes densified per block.

    Returns:
        A tuple of four arrays, each of length ``n_genes``:
        ``(statistic, pvalue, testable, untestable_reason)``. ``statistic`` is
        the Mann-Whitney U for the selected group. ``testable`` is False for
        genes excluded from the BH denominator, and ``untestable_reason``
        carries a short reason code (empty string when testable).
    """

    n_genes = int(matrix.shape[1]) if matrix.ndim == 2 else 0
    if n_genes == 0:
        return _empty_test_result(0, REASON_EMPTY_MATRIX)

    mask = np.asarray(selected, dtype=bool).ravel()
    n_selected = int(mask.sum())
    n_reference = int(mask.size - n_selected)

    if (
        n_selected < MIN_SPOTS_PER_GROUP
        or n_reference < MIN_SPOTS_PER_GROUP
    ):
        logger.debug(
            "Skipping rank-sum test: %d selected / %d reference spots "
            "(minimum %d per side).",
            n_selected,
            n_reference,
            MIN_SPOTS_PER_GROUP,
        )
        return _empty_test_result(n_genes, REASON_INSUFFICIENT_SPOTS)

    statistic = np.zeros(n_genes, dtype=np.float64)
    pvalue = np.ones(n_genes, dtype=np.float64)
    testable = np.zeros(n_genes, dtype=bool)
    reasons = np.full(n_genes, "", dtype=object)

    is_sparse = sp.issparse(matrix)
    # CSC makes column-block slicing cheap. The conversion copies index arrays
    # but preserves sparsity — it does not densify.
    working = matrix.tocsc() if is_sparse else np.asarray(matrix)

    step = max(1, int(chunk_size))
    for start in range(0, n_genes, step):
        stop = min(start + step, n_genes)
        block = working[:, start:stop]
        dense_block = np.asarray(
            block.toarray() if sp.issparse(block) else block,
            dtype=np.float64,
        )

        # A gene constant across every spot has zero tie-corrected variance.
        block_min = dense_block.min(axis=0)
        block_max = dense_block.max(axis=0)
        constant = block_min == block_max
        varying = ~constant

        idx = np.arange(start, stop)
        if constant.any():
            constant_idx = idx[constant]
            statistic[constant_idx] = 0.5 * n_selected * n_reference
            pvalue[constant_idx] = 1.0
            testable[constant_idx] = False
            reasons[constant_idx] = REASON_CONSTANT

        if not varying.any():
            continue

        with warnings.catch_warnings():
            # A degenerate block should never surface as a runtime warning to
            # the user; the values are sanitised immediately below.
            warnings.simplefilter("ignore")
            block_stat, block_p = _rank_sum_block(
                dense_block[:, varying],
                mask,
                n_selected,
                n_reference,
            )

        # Belt and braces: the constant-gene guard above should make this
        # unreachable, but a NaN escaping into the output dict would be far
        # worse than a conservative 1.0.
        bad = ~np.isfinite(block_p)
        if bad.any():
            logger.warning(
                "Rank-sum produced %d non-finite p-values; coercing to 1.0.",
                int(bad.sum()),
            )
        block_p = np.where(bad, 1.0, block_p)
        block_p = np.clip(block_p, 0.0, 1.0)
        block_stat = np.where(np.isfinite(block_stat), block_stat, 0.0)

        varying_idx = idx[varying]
        statistic[varying_idx] = block_stat
        pvalue[varying_idx] = block_p
        testable[varying_idx] = ~bad
        reasons[varying_idx] = np.where(bad, REASON_NON_FINITE, "")

    return statistic, pvalue, testable, reasons


def adjust_pvalues(
    pvalues: np.ndarray,
    testable: np.ndarray,
) -> tuple[np.ndarray, int]:
    """Apply BH correction over testable genes only (T-009, refinement R1).

    Genes that could not be meaningfully tested are excluded from the
    denominator rather than counted as tests that happened to be
    non-significant. Including them would inflate ``m`` and make every real
    correction needlessly conservative. Excluded genes receive
    ``adj_pvalue = 1.0``.

    Args:
        pvalues: Raw p-values for every gene.
        testable: Boolean mask; True where the gene was actually tested.

    Returns:
        A tuple of (adjusted p-values for every gene, BH denominator used).
        The denominator is the count of testable genes and is surfaced as
        ``DEGResult.n_genes_tested``.
    """

    raw = np.asarray(pvalues, dtype=np.float64).ravel()
    mask = np.asarray(testable, dtype=bool).ravel()
    adjusted = np.ones(raw.size, dtype=np.float64)

    n_tested = int(mask.sum())
    if n_tested == 0:
        return adjusted, 0

    adjusted[mask] = benjamini_hochberg(raw[mask], n_tests=n_tested)
    return adjusted, n_tested


__all__ = [
    "DEFAULT_CHUNK_SIZE",
    "MIN_SPOTS_PER_GROUP",
    "REASON_CONSTANT",
    "REASON_EMPTY_MATRIX",
    "REASON_INSUFFICIENT_SPOTS",
    "REASON_NON_FINITE",
    "adjust_pvalues",
    "benjamini_hochberg",
    "wilcoxon_rank_sum",
]
