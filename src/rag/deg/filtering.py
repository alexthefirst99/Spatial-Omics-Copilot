"""
DEG Candidate Pre-Filtering (T-010)
===================================
Drops genes that are detected in too few spots BEFORE any statistical test
runs. This is both a performance measure and a statistical one: every gene
that survives costs one hypothesis test and therefore inflates the
Benjamini-Hochberg denominator, so removing genes that cannot carry evidence
makes the surviving corrections less conservative.

The filter operates over the UNION of selected and reference spots, i.e. the
whole matrix passed in. It never looks at the ROI mask, so it cannot leak
group membership into the test and bias it.

Memory contract: this module never calls ``.toarray()`` / ``.todense()`` on a
full matrix. A VisiumHD section is ~30 000 spots x ~18 000 genes; densifying
that at float64 is ~4.3 GB. Detection counts are computed with sparse
reductions only.

Mutation contract: the caller's AnnData is never modified. See
``filter_deg_candidates`` for the view-vs-copy decision.
"""

from __future__ import annotations

import logging

import numpy as np
import scipy.sparse as sp
from anndata import AnnData

logger = logging.getLogger(__name__)


def gene_detection_counts(matrix: sp.spmatrix | np.ndarray) -> np.ndarray:
    """Count, per gene, how many spots express it (value strictly > 0).

    Two sparse strategies are available and the cheaper one is chosen at
    runtime:

    * ``matrix.getnnz(axis=0)`` touches only the index arrays and allocates no
      temporary matrix, but it counts STORED entries. A stored explicit zero,
      or a negative value, would be miscounted.
    * ``(matrix > 0).sum(axis=0)`` is always correct but materialises a boolean
      sparse copy with the same sparsity structure.

    ``getnnz`` is therefore used only after confirming via ``data.min() > 0``
    that every stored value is strictly positive, which makes the two
    definitions identical. That check is a reduction, so it allocates nothing —
    unlike ``(data <= 0).any()``, which would allocate a boolean array as large
    as ``nnz``. The obvious alternative, calling ``eliminate_zeros()`` first,
    is deliberately NOT used: it mutates the caller's matrix in place and would
    break this module's no-mutation contract.

    Args:
        matrix: Expression matrix, spots x genes, sparse or dense.

    Returns:
        A 1-D int array of length ``n_genes`` with per-gene detection counts.
    """

    if sp.issparse(matrix):
        data = matrix.data
        if data.size == 0:
            return np.zeros(matrix.shape[1], dtype=np.int64)
        if float(data.min()) > 0.0:
            return np.asarray(matrix.getnnz(axis=0), dtype=np.int64)
        return np.asarray((matrix > 0).sum(axis=0), dtype=np.int64).ravel()

    dense = np.asarray(matrix)
    if dense.size == 0:
        return np.zeros(dense.shape[1] if dense.ndim == 2 else 0, dtype=np.int64)
    return np.asarray((dense > 0).sum(axis=0), dtype=np.int64).ravel()


def candidate_gene_mask(adata: AnnData, min_cells: int = 10) -> np.ndarray:
    """Return a boolean mask over genes that pass the detection threshold.

    Args:
        adata: Spots x genes AnnData. Not modified.
        min_cells: Minimum number of spots in which a gene must be detected.
            Values <= 0 disable filtering and keep every gene.

    Returns:
        A boolean array of length ``adata.n_vars``; True means "keep".
    """

    n_genes = int(adata.n_vars)
    if min_cells <= 0 or n_genes == 0:
        return np.ones(n_genes, dtype=bool)

    counts = gene_detection_counts(adata.X)
    if counts.size != n_genes:
        # Defensive: a malformed matrix should not silently mis-filter.
        logger.warning(
            "Detection-count length %d does not match n_vars %d; "
            "skipping pre-filter.",
            counts.size,
            n_genes,
        )
        return np.ones(n_genes, dtype=bool)
    return counts >= int(min_cells)


def filter_deg_candidates(adata: AnnData, min_cells: int = 10) -> AnnData:
    """Drop genes detected in fewer than ``min_cells`` spots (T-010).

    Returns a **view**, not a copy. AnnData views hold a reference plus an
    index; no expression data is duplicated and, critically, nothing is
    densified. The caller's object is never mutated — the ``min_cells``
    threshold is applied by slicing, not by in-place filtering, so this is
    safe to call on an AnnData the caller still intends to use.

    Callers that need the number of genes removed (for the auditable BH
    denominator) should use :func:`filter_deg_candidates_with_count`, which
    returns the same view alongside the count.

    Args:
        adata: Spots x genes AnnData. Not modified.
        min_cells: Minimum spots in which a gene must be detected to survive.

    Returns:
        An AnnData view restricted to the surviving genes.
    """

    return filter_deg_candidates_with_count(adata, min_cells)[0]


def filter_deg_candidates_with_count(
    adata: AnnData,
    min_cells: int = 10,
) -> tuple[AnnData, int]:
    """Pre-filter genes and report how many were removed.

    The removal count is surfaced in ``DEGResult.n_genes_filtered_out`` so the
    number of hypothesis tests actually performed is auditable from the output
    alone.

    Args:
        adata: Spots x genes AnnData. Not modified.
        min_cells: Minimum spots in which a gene must be detected to survive.

    Returns:
        A tuple of (filtered AnnData view, number of genes removed).
    """

    mask = candidate_gene_mask(adata, min_cells)
    removed = int(mask.size - int(mask.sum()))
    if removed == 0:
        return adata, 0
    logger.debug(
        "Pre-filter removed %d/%d genes below min_cells=%d.",
        removed,
        mask.size,
        min_cells,
    )
    return adata[:, mask], removed


__all__ = [
    "candidate_gene_mask",
    "filter_deg_candidates",
    "filter_deg_candidates_with_count",
    "gene_detection_counts",
]
