"""Shared result contracts for the RAG pipeline (Person 6 / Alex).

Every module in ``src/rag`` returns one of these instead of an ad hoc dict, so
the integration pipeline in ``rag.pipeline`` can pass one person's output
directly into the next person's function. ``DEGResult``/``GeneStat`` were
previously defined in ``rag.deg.models`` and ``AgentResult`` (with
``TraceStep``/``Citation``/``PathwayBar``/``DegBar``) in
``rag.copilot_agent.models`` — both modules left an explicit note that those
were provisional stand-ins for this file, so they now import the canonical
definitions from here instead of redefining them.

Call ``.to_dict()`` on any of these for the plain-dict form existing callers
(``app.py``, ``routes.py``, ``chat.js``) already expect.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from math import isfinite
from typing import Any


# ---------------------------------------------------------------------------
# Shared coercion helpers (moved from rag.copilot_agent.models)
# ---------------------------------------------------------------------------


def _as_text(value: object) -> str:
    """Return a clean single-line string, never ``None``."""

    if value is None:
        return ""
    return " ".join(str(value).split())


def _as_float(value: object, default: float = 0.0) -> float:
    """Coerce to a finite float. Never returns ``None``, ``NaN`` or infinity."""

    if isinstance(value, bool) or value is None:
        return default
    try:
        number = float(value)
    except (TypeError, ValueError):
        return default
    return number if isfinite(number) else default


def _as_int(value: object, default: int = 0) -> int:
    """Coerce to an int, falling back to ``default`` on anything unusable."""

    if isinstance(value, bool) or value is None:
        return default
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


# ---------------------------------------------------------------------------
# Preprocessing (T-035) / Clustering (T-040)
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class PreprocessResult:
    """Outcome of ``preprocess_h5ad()`` — a cached, preprocessed AnnData file."""

    adata_path: str
    n_spots: int
    n_genes: int
    from_cache: bool = False

    def to_dict(self) -> dict[str, Any]:
        return {
            "adata_path": self.adata_path,
            "qc_summary": {"n_spots": self.n_spots, "n_genes": self.n_genes},
            "from_cache": self.from_cache,
        }


@dataclass(frozen=True, slots=True)
class ClusterResult:
    """Outcome of ``cluster_adata()`` — spatial cluster assignments for an AnnData file."""

    adata_path: str
    cluster_path: str
    method: str
    n_clusters: int
    n_spots: int

    def to_dict(self) -> dict[str, Any]:
        return {
            "adata_path": self.adata_path,
            "cluster_path": self.cluster_path,
            "cluster_summary": {
                "method": self.method,
                "n_clusters": self.n_clusters,
                "n_spots": self.n_spots,
            },
        }


# ---------------------------------------------------------------------------
# ROI selection (T-052) / ROI image preparation (T-045)
# ---------------------------------------------------------------------------


@dataclass(slots=True)
class ROISelection:
    """A user-selected region, resolved down to the spots/barcodes inside it.

    ``selection_type`` is ``"polygon"`` or ``"cluster"``; the matching one of
    ``polygon_points`` / ``cluster_id`` is populated. ``spot_ids`` and
    ``barcode_ids`` both hold the same ``adata.obs_names`` values — two names
    because ``rag.copilot_agent.prompt`` reads either, matching however the
    dataset labels its rows.
    """

    roi_id: str = ""
    selection_type: str = ""
    polygon_points: list | None = None
    cluster_id: str | None = None
    spot_ids: list[str] = field(default_factory=list)
    barcode_ids: list[str] = field(default_factory=list)
    status_message: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "roi_id": self.roi_id,
            "selection_type": self.selection_type,
            "polygon_points": self.polygon_points,
            "cluster_id": self.cluster_id,
            "spot_ids": list(self.spot_ids),
            "barcode_ids": list(self.barcode_ids),
            "status_message": self.status_message,
        }


@dataclass(slots=True)
class ROIImageResult:
    """A cropped H&E image prepared for a vision-capable LLM."""

    roi_id: str = ""
    crop_path: str = ""
    width: int = 0
    height: int = 0
    image_format: str = ""
    scale_factor: float = 1.0
    status_message: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "roi_id": self.roi_id,
            "crop_path": self.crop_path,
            "width": self.width,
            "height": self.height,
            "image_format": self.image_format,
            "scale_factor": self.scale_factor,
            "status_message": self.status_message,
        }


# ---------------------------------------------------------------------------
# DEG (T-008 / T-009 / T-044) — canonical home; rag.deg.models re-exports these
# ---------------------------------------------------------------------------

# Generic outcomes shared with the agent's tool-call statuses below.
STATUS_OK = "ok"
STATUS_ERROR = "error"

# DEG-specific outcomes.
STATUS_NO_DATA = "no_data"
STATUS_EMPTY_SELECTION = "empty_selection"
# No spot barcode in the dataset appeared in the cluster assignment at all,
# which means the two files describe different datasets. Distinct from
# STATUS_EMPTY_SELECTION, where the barcodes matched and the cluster is simply
# empty.
STATUS_BARCODE_MISMATCH = "barcode_mismatch"
STATUS_NO_SIGNIFICANT = "no_significant_genes"
STATUS_INVALID_INPUT = "invalid_input"

# Exact wording required by T-044 so a caller can distinguish "nothing was
# loaded" from "analysis ran and found nothing".
MESSAGE_NO_DATA = "No gene expression data loaded."

# Observed-provenance labels for the matrix the statistics actually ran on.
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
        """Return the per-gene dict shape consumed by pipeline.py and app.py."""

        return {
            "gene": self.gene,
            "mean_expression": self.mean_expression,
            "pct_spots_expressed": self.pct_spots_expressed,
            # Legacy aliases for existing callers.
            "mean_roi": self.mean_expression,
            "mean_reference": self.mean_reference,
            "pct_roi": self.pct_spots_expressed,
            "pct_reference": self.pct_reference,
            "log2_fold_change": self.log2_fold_change,
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
    raising.

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
            ``log2_fold_change > 0``.
        fdr_applied: Whether ``top_genes`` was actually filtered by FDR.
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
        """Return the dict shape consumed by pipeline.py, routes.py and app.py."""

        payload: dict[str, Any] = {
            "selected_spots": self.selected_spots,
            "reference_spots": self.reference_spots,
            "total_spots": self.total_spots,
            "top_genes": [gene.to_dict() for gene in self.top_genes],
            "ranking_method": self.ranking_method,
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


# ---------------------------------------------------------------------------
# Agent (T-020 - T-023, T-046, T-047) — canonical home; rag.copilot_agent.models
# re-exports these
# ---------------------------------------------------------------------------

# Trace icons. app/assets/chat.js never reads trace[].icon — every row gets a
# hardcoded green check — so these are advisory metadata for future UI work.
ICON_DEG = "deg"
ICON_PATHWAY = "pathway"
ICON_PUBMED = "pubmed"
ICON_GENE = "gene"
ICON_IMAGE = "image"
ICON_AGENT = "agent"

# Tool-call outcomes recorded in the trace (T-022).
STATUS_EMPTY = "empty"
STATUS_SKIPPED = "skipped"


@dataclass(frozen=True, slots=True)
class TraceStep:
    """One recorded agent action (T-022).

    Attributes:
        step: Row text in the AGENT TRACE card.
        detail: Monospace grey suffix. Omitted by the UI when empty.
        icon: Advisory category; not read by the current front-end.
        tool: Tool name, or "" for non-tool steps such as routing.
        status: One of ``STATUS_OK`` / ``STATUS_EMPTY`` / ``STATUS_ERROR`` /
            ``STATUS_SKIPPED``.
        input_summary: Short description of what the tool was called with.
        output_summary: Short description of what came back.
    """

    step: str
    detail: str = ""
    icon: str = ICON_AGENT
    tool: str = ""
    status: str = STATUS_OK
    input_summary: str = ""
    output_summary: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "step": _as_text(self.step),
            "detail": _as_text(self.detail),
            "icon": _as_text(self.icon) or ICON_AGENT,
            "tool": _as_text(self.tool),
            "status": _as_text(self.status) or STATUS_OK,
            "input_summary": _as_text(self.input_summary),
            "output_summary": _as_text(self.output_summary),
        }


@dataclass(frozen=True, slots=True)
class Citation:
    """A PubMed record the agent actually retrieved this turn."""

    id: int
    pmid: str = ""
    title: str = ""
    journal: str = ""
    year: int | None = None

    @property
    def url(self) -> str:
        """Canonical PubMed URL, or "" when there is no PMID."""

        return f"https://pubmed.ncbi.nlm.nih.gov/{self.pmid}/" if self.pmid else ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": _as_int(self.id),
            "pmid": _as_text(self.pmid),
            "title": _as_text(self.title),
            "journal": _as_text(self.journal),
            "year": self.year if isinstance(self.year, int) else None,
            "url": self.url,
        }


@dataclass(frozen=True, slots=True)
class PathwayBar:
    """One row of the ENRICHED PATHWAYS panel."""

    source: str
    name: str
    neg_log10p: float
    gene_count: int = 0

    def to_dict(self) -> dict[str, Any]:
        return {
            "source": _as_text(self.source),
            "name": _as_text(self.name),
            "neg_log10p": round(_as_float(self.neg_log10p), 1),
            "gene_count": _as_int(self.gene_count),
        }


@dataclass(frozen=True, slots=True)
class DegBar:
    """One row of the TOP DEGs panel."""

    gene: str
    log2fc: float

    def to_dict(self) -> dict[str, Any]:
        return {
            "gene": _as_text(self.gene),
            "log2fc": round(_as_float(self.log2fc), 2),
        }


@dataclass(slots=True)
class AgentResult:
    """Everything one agent turn produced.

    Attributes:
        answer: Synthesized answer text. Empty when the LLM call is delegated
            to ``app/worker.py`` (the default).
        trace: Ordered record of what the agent actually did (T-022).
        citations: PubMed records retrieved this turn.
        used_roi_image: Whether the cropped ROI image was part of the model
            input.
        context_str: Evidence block appended to the user prompt by
            ``app/worker.py``. MUST begin with "\\n\\n".
        gene_objects: The DEG list echoed back, unchanged.
        degs: Bar-chart rows for the TOP DEGs panel.
        pathways: Bar-chart rows for the ENRICHED PATHWAYS panel.
        label: Region label for panel headers, e.g. "Cluster 5" or "ROI".
        intent: Routing intent label from ``copilot_agent.routing``.
        tools_called: Tool names actually invoked, in call order.
        status_message: Human-readable state, surfaced when evidence is missing.
    """

    answer: str = ""
    trace: list[TraceStep] = field(default_factory=list)
    citations: list[Citation] = field(default_factory=list)
    used_roi_image: bool = False
    context_str: str = ""
    gene_objects: list[dict] = field(default_factory=list)
    degs: list[DegBar] = field(default_factory=list)
    pathways: list[PathwayBar] = field(default_factory=list)
    label: str = "selection"
    intent: str = ""
    tools_called: list[str] = field(default_factory=list)
    status_message: str = ""

    @property
    def ok(self) -> bool:
        """Whether the turn produced any evidence at all."""

        return bool(self.degs or self.pathways or self.citations)

    def to_metadata(self) -> dict[str, Any]:
        """Return the ``metadata`` block consumed by chat.js."""

        return {
            "trace": [step.to_dict() for step in self.trace],
            "degs": [bar.to_dict() for bar in self.degs],
            "pathways": [bar.to_dict() for bar in self.pathways],
            "citations": [citation.to_dict() for citation in self.citations],
            "label": _as_text(self.label) or "selection",
            "intent": _as_text(self.intent),
            "tools_called": list(self.tools_called),
            "used_roi_image": bool(self.used_roi_image),
            "status_message": _as_text(self.status_message),
        }

    def to_legacy_dict(self) -> dict[str, Any]:
        """Return the exact dict ``app/routes.py`` expects."""

        context = self.context_str or ""
        if context and not context.startswith("\n\n"):
            context = "\n\n" + context.lstrip("\n")

        return {
            "gene_objects": list(self.gene_objects),
            "context_str": context,
            "metadata": self.to_metadata(),
        }

    def to_dict(self) -> dict[str, Any]:
        """Return the richer plan-level view used by the integration pipeline."""

        return {
            "answer": self.answer,
            "trace": [step.to_dict() for step in self.trace],
            "citations": [citation.to_dict() for citation in self.citations],
            "used_roi_image": bool(self.used_roi_image),
            "context_str": self.context_str,
            "intent": _as_text(self.intent),
            "tools_called": list(self.tools_called),
            "label": _as_text(self.label) or "selection",
            "status_message": _as_text(self.status_message),
        }


__all__ = [
    "ICON_AGENT",
    "ICON_DEG",
    "ICON_GENE",
    "ICON_IMAGE",
    "ICON_PATHWAY",
    "ICON_PUBMED",
    "MESSAGE_NO_DATA",
    "SOURCE_EMPTY",
    "SOURCE_RAW_COUNTS",
    "SOURCE_UNKNOWN",
    "STATUS_BARCODE_MISMATCH",
    "STATUS_EMPTY",
    "STATUS_EMPTY_SELECTION",
    "STATUS_ERROR",
    "STATUS_INVALID_INPUT",
    "STATUS_NO_DATA",
    "STATUS_NO_SIGNIFICANT",
    "STATUS_OK",
    "STATUS_SKIPPED",
    "AgentResult",
    "Citation",
    "ClusterResult",
    "DEGResult",
    "DegBar",
    "GeneStat",
    "PathwayBar",
    "PreprocessResult",
    "ROIImageResult",
    "ROISelection",
    "TraceStep",
]
