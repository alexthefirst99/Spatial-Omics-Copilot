"""
DEG Result Models
=================
PROVISIONAL — these dataclasses are a local stand-in for the shared result
contract. Person 6 owns ``src/rag/contracts.py``; once ``contracts.DEGResult``
lands, ``GeneStat`` and ``DEGResult`` here should be deleted and the shared
types imported instead. Nothing outside ``rag.deg`` should depend on these
classes directly — depend on ``to_dict()`` output, which is the stable surface.

``to_dict()`` deliberately emits a SUPERSET of the dict shape that
``docs/specs.md`` section 3.1 documents and that ``rag.pipeline`` and
``app.py`` consume today. Every pre-existing key keeps its original meaning;
statistical fields are added alongside them.

Legacy aliases: ``mean_roi`` duplicates ``mean_expression`` and ``pct_roi``
duplicates ``pct_spots_expressed``. They are emitted only for backward
compatibility and are flagged for removal in
``docs/validation/person2_deg_notes.md``.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


# Status codes. ``status_message`` is the human-readable form; ``status`` is
# the machine-readable discriminator a caller should branch on.
STATUS_OK = "ok"
STATUS_NO_DATA = "no_data"
STATUS_EMPTY_SELECTION = "empty_selection"
# No spot barcode in the dataset appeared in the cluster assignment at all,
# which means the two files describe different datasets. Distinct from
# STATUS_EMPTY_SELECTION, where the barcodes matched and the cluster is simply
# empty.
STATUS_BARCODE_MISMATCH = "barcode_mismatch"
STATUS_NO_SIGNIFICANT = "no_significant_genes"
STATUS_INVALID_INPUT = "invalid_input"
STATUS_ERROR = "error"

# Exact wording required by T-044 so a caller can distinguish "nothing was
# loaded" from "analysis ran and found nothing".
MESSAGE_NO_DATA = "No gene expression data loaded."

# Observed-provenance labels for the matrix the statistics actually ran on.
# These describe what was measured, never what is assumed about the upstream
# pipeline. See the normalization caveat in the module docstring of
# ``rag.deg.extraction``.
SOURCE_RAW_COUNTS = "raw_counts_unnormalized"
SOURCE_UNKNOWN = "non_integer_values_provenance_unknown"
SOURCE_EMPTY = "no_values_observed"


@dataclass(frozen=True, slots=True)
class GeneStat:
    """Per-gene effect size and test result.

    Attributes:
        gene: Gene symbol as it appears in ``adata.var_names``.
        log2_fold_change: log2((mean_selected + eps) / (mean_reference + eps)),
            computed from the same matrix the rank-sum test ranked.
        mean_expression: Mean expression across selected spots.
        pct_spots_expressed: Fraction of selected spots with a nonzero value.
        mean_reference: Mean expression across reference spots.
        pct_reference: Fraction of reference spots with a nonzero value.
        pvalue: Two-sided Mann-Whitney U p-value, or 1.0 when untestable.
        adj_pvalue: Benjamini-Hochberg adjusted p-value, or 1.0 when untestable.
        statistic: Mann-Whitney U statistic for the selected group.
        testable: False when the gene was excluded from the BH denominator.
        untestable_reason: Short machine-readable reason when ``testable`` is
            False; empty string otherwise.
    """

    gene: str
    log2_fold_change: float = 0.0
    mean_expression: float = 0.0
    pct_spots_expressed: float = 0.0
    mean_reference: float = 0.0
    pct_reference: float = 0.0
    pvalue: float = 1.0
    adj_pvalue: float = 1.0
    statistic: float = 0.0
    testable: bool = False
    untestable_reason: str = ""

    def to_dict(self) -> dict[str, Any]:
        """Return the per-gene dict shape consumed by pipeline.py and app.py.

        Returns:
            A dict containing every key the current implementation emits, plus
            ``pvalue``, ``adj_pvalue``, ``statistic``, ``testable`` and
            ``untestable_reason``.
        """

        return {
            "gene": self.gene,
            "mean_expression": self.mean_expression,
            "pct_spots_expressed": self.pct_spots_expressed,
            # Legacy aliases — see module docstring.
            "mean_roi": self.mean_expression,
            "mean_reference": self.mean_reference,
            "pct_roi": self.pct_spots_expressed,
            "pct_reference": self.pct_reference,
            "log2_fold_change": self.log2_fold_change,
            # Added by T-008 / T-009.
            "pvalue": self.pvalue,
            "adj_pvalue": self.adj_pvalue,
            "statistic": self.statistic,
            "testable": self.testable,
            "untestable_reason": self.untestable_reason,
        }


@dataclass(slots=True)
class DEGResult:
    """Envelope for a differential-expression run.

    A ``DEGResult`` is always safe to return: failure paths produce an empty
    ``top_genes`` list plus a ``status`` / ``status_message`` pair rather than
    raising. See ``rag.deg.extraction`` for the guarantees.

    Attributes:
        selected_spots: Number of spots inside the ROI / cluster.
        reference_spots: Number of spots outside it.
        total_spots: Total spots in the dataset.
        ranking_method: Label describing how ``top_genes`` was ordered.
        top_genes: Ranked ``GeneStat`` records.
        status: One of the ``STATUS_*`` constants.
        status_message: Human-readable explanation, safe for UI/LLM display.
        n_genes_input: Genes present before pre-filtering.
        n_genes_tested: Genes actually tested — this IS the BH denominator.
        n_genes_filtered_out: Genes dropped by the ``min_cells`` pre-filter.
        n_genes_untestable: Genes kept by the pre-filter but excluded from the
            BH denominator (constant expression, or too few spots per side).
        n_significant: Testable genes with ``adj_pvalue`` below
            ``fdr_threshold`` in EITHER direction. This can exceed
            ``len(top_genes)``, because ``top_genes`` additionally requires
            ``log2_fold_change > 0`` — a gene significantly DEPLETED in the
            selection counts here but is not reported as a marker.
        fdr_applied: Whether ``top_genes`` was actually filtered by FDR. False
            means the list is a ranked but statistically UNFILTERED list.
        fdr_threshold: Threshold used, or None when no filtering was applied.
        expression_source: Observed provenance of the matrix used.
        min_cells: The ``min_cells`` pre-filter value that was applied.
        cluster_id: Cluster label, when the run was cluster-based.
        cluster_key: ``adata.obs`` key the cluster labels came from.
    """

    selected_spots: int = 0
    reference_spots: int = 0
    total_spots: int = 0
    ranking_method: str = ""
    top_genes: list[GeneStat] = field(default_factory=list)
    status: str = STATUS_OK
    status_message: str = ""
    n_genes_input: int = 0
    n_genes_tested: int = 0
    n_genes_filtered_out: int = 0
    n_genes_untestable: int = 0
    n_significant: int = 0
    fdr_applied: bool = False
    fdr_threshold: float | None = None
    expression_source: str = SOURCE_EMPTY
    min_cells: int = 0
    cluster_id: str | None = None
    cluster_key: str | None = None

    @property
    def ok(self) -> bool:
        """Whether the run produced at least one ranked gene."""

        return bool(self.top_genes)

    def to_dict(self) -> dict[str, Any]:
        """Return the dict shape consumed by pipeline.py, routes.py and app.py.

        Returns:
            A superset of the legacy contract. ``cluster_id`` / ``cluster_key``
            are present only for cluster-based runs, matching current
            behaviour.
        """

        payload: dict[str, Any] = {
            # Legacy contract — unchanged meaning.
            "selected_spots": self.selected_spots,
            "reference_spots": self.reference_spots,
            "total_spots": self.total_spots,
            "top_genes": [gene.to_dict() for gene in self.top_genes],
            "ranking_method": self.ranking_method,
            # Added by T-008 / T-009 / T-044.
            "status": self.status,
            "status_message": self.status_message,
            "n_genes_input": self.n_genes_input,
            "n_genes_tested": self.n_genes_tested,
            "n_genes_filtered_out": self.n_genes_filtered_out,
            "n_genes_untestable": self.n_genes_untestable,
            "n_significant": self.n_significant,
            "fdr_applied": self.fdr_applied,
            "fdr_threshold": self.fdr_threshold,
            "expression_source": self.expression_source,
            "min_cells": self.min_cells,
        }
        if self.cluster_id is not None:
            payload["cluster_id"] = self.cluster_id
        if self.cluster_key is not None:
            payload["cluster_key"] = self.cluster_key
        return payload


__all__ = [
    "DEGResult",
    "GeneStat",
    "MESSAGE_NO_DATA",
    "SOURCE_EMPTY",
    "SOURCE_RAW_COUNTS",
    "SOURCE_UNKNOWN",
    "STATUS_BARCODE_MISMATCH",
    "STATUS_EMPTY_SELECTION",
    "STATUS_ERROR",
    "STATUS_INVALID_INPUT",
    "STATUS_NO_DATA",
    "STATUS_NO_SIGNIFICANT",
    "STATUS_OK",
]
