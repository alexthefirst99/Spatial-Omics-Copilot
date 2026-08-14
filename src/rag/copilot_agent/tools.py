"""LangChain tool definitions for the copilot agent (T-020).

Three tools are exposed: ``gene_annotation_tool``, ``pathway_tool`` and
``pubmed_tool``. DEG extraction is deliberately **not** a tool — it runs in
``app/app.py`` the moment the user selects a cluster or draws an ROI, long
before any chat message exists (``docs/rules.md`` section 4).

Two design rules drive this module:

**A tool must never raise.** ``docs/rules.md`` section 4 requires tools to
return empty results rather than propagate exceptions, and LangGraph offers no
help here — ``handle_tool_error=True`` only catches ``ToolException``, so a
``requests`` timeout or a pydantic error would otherwise crash the graph node
and lose the user's turn. Note that ``run_pathway_enrichment`` and
``run_gene_annotation_retrieval`` also raise ``ValueError`` on malformed
*config* values (their ``int()`` / ``float()`` coercions sit outside their own
try blocks), so guarding network errors alone is not enough.

**Upstream imports are lazy.** They happen inside the functions so that a
missing optional dependency degrades one tool instead of breaking the whole
agent package at import time — which would take every test down at collection.

Every call returns a :class:`ToolOutcome` carrying the raw upstream envelope
plus the summaries T-022 needs for the trace.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from rag.copilot_agent import adapters
from rag.copilot_agent.models import (
    STATUS_EMPTY,
    STATUS_ERROR,
    STATUS_OK,
)
from rag.copilot_agent.routing import (
    TOOL_GENE_ANNOTATION,
    TOOL_PATHWAY,
    TOOL_PUBMED,
)

# Default for the colorectal demo dataset; callers may override it.
DEFAULT_DISEASE = "colorectal cancer"

# Bound PubMed latency while calls remain on the request path.
DEFAULT_PUBMED_TIMEOUT = 10.0
DEFAULT_PUBMED_RETRIES = 1
DEFAULT_PUBMED_CANDIDATE_POOL = 15

DEFAULT_TOP_PATHWAYS = 6
DEFAULT_MAX_ABSTRACTS = 3
# NCBI Gene truncates its input list to max_genes (default 10), so sending more
# than this silently drops the tail rather than erroring.
DEFAULT_MAX_ANNOTATED_GENES = 10


@dataclass(slots=True)
class ToolOutcome:
    """The result of one tool invocation, plus everything the trace needs.

    Attributes:
        tool: Tool name, matching the ``TOOL_*`` constants.
        result: The raw upstream envelope (``PathwayResult``,
            ``GeneAnnotationResult``, ``PubMedResult``) or ``None`` on failure.
        status: ``STATUS_OK`` when rows came back, ``STATUS_EMPTY`` when the
            call succeeded but found nothing, ``STATUS_ERROR`` on failure.
        detail: Short suffix rendered next to the trace row.
        input_summary: What the tool was called with (T-022).
        output_summary: What came back (T-022).
        error: Exception text when ``status`` is ``STATUS_ERROR``, else "".
        extras: Tool-specific payload, e.g. the PubMed query string.
    """

    tool: str
    result: Any = None
    status: str = STATUS_EMPTY
    detail: str = ""
    input_summary: str = ""
    output_summary: str = ""
    error: str = ""
    extras: dict[str, Any] = field(default_factory=dict)

    @property
    def ok(self) -> bool:
        """Whether the tool returned usable evidence."""

        return self.status == STATUS_OK


def _genes_summary(genes: list[str], limit: int = 5) -> str:
    """Render a gene list for a trace line without letting it run long."""

    if not genes:
        return "no genes"
    head = ", ".join(genes[:limit])
    if len(genes) > limit:
        head += f" +{len(genes) - limit} more"
    return head


def _is_failure_status_message(status_message: str) -> bool:
    """Tell "the call failed" apart from "the call succeeded and found nothing".

    rag.pathway_enrichment / rag.gene_annotation / rag.pubmed_retrieval all
    put the word "unavailable" in their status_message specifically and only
    for genuine failures (network errors, SSL errors, timeouts) — a
    successful-but-empty result uses different wording ("No matching ...
    were found.", "... but none passed the cutoff."). Without this check, a
    tool with zero rows always reports STATUS_EMPTY even when the real cause
    was a connection failure, silently presenting "no results found" to the
    user in place of an error.
    """

    return "unavailable" in (status_message or "").lower()


# -- Pathway enrichment (T-012 upstream) -----------------


def run_pathway_tool(
    genes: list[str],
    config: object | None = None,
    top_n: int = DEFAULT_TOP_PATHWAYS,
) -> ToolOutcome:
    """Run GO / KEGG over-representation analysis on the ROI gene list.

    Args:
        genes: Ranked gene symbols from the DEG result.
        config: Application config. ``run_pathway_enrichment`` reads its values
            from the ``"pathway_enrichment"`` key; it never loads
            ``config/app.yaml`` itself, so the caller must pass it through.
        top_n: Maximum pathways to keep.

    Returns:
        A :class:`ToolOutcome` whose ``result`` is a ``PathwayResult``, or
        ``None`` if the module could not run at all.
    """

    outcome = ToolOutcome(tool=TOOL_PATHWAY, input_summary=_genes_summary(genes))

    if not genes:
        outcome.detail = "skipped — no genes"
        outcome.output_summary = "no input genes"
        return outcome

    try:
        from rag.pathway_enrichment import run_pathway_enrichment

        merged = _with_override(config, "pathway_enrichment", {"top_n": top_n})
        result = run_pathway_enrichment(list(genes), config=merged)
    except Exception as exc:  # noqa: BLE001 — a tool must never raise.
        outcome.status = STATUS_ERROR
        outcome.error = f"{type(exc).__name__}: {exc}"
        outcome.detail = "unavailable"
        outcome.output_summary = f"failed ({type(exc).__name__})"
        return outcome

    outcome.result = result
    rows = adapters.iter_pathways(result)
    status_message = adapters.status_of(result)

    if rows:
        outcome.status = STATUS_OK
        sources = ", ".join(
            sorted({adapters.shorten_source(adapters.get_field(r, "source")) for r in rows} - {""})
        )
        outcome.detail = sources or "GO · KEGG"
        outcome.output_summary = f"{len(rows)} enriched pathway(s)"
    elif _is_failure_status_message(status_message):
        outcome.status = STATUS_ERROR
        outcome.error = status_message
        outcome.detail = "unavailable"
        outcome.output_summary = status_message
    else:
        outcome.status = STATUS_EMPTY
        outcome.detail = "no enriched pathways"
        outcome.output_summary = status_message or "no pathways passed the cutoff"

    return outcome


# -- Gene annotation (T-049 upstream) ------------------


def run_gene_annotation_tool(
    genes: list[str],
    config: object | None = None,
    max_genes: int = DEFAULT_MAX_ANNOTATED_GENES,
) -> ToolOutcome:
    """Retrieve NCBI Gene annotations for the top ROI genes.

    Args:
        genes: Ranked gene symbols. Only the first ``max_genes`` are sent —
            NCBI Gene truncates silently beyond its own limit, so the ranking
            order matters.
        config: Application config; values are read from the
            ``"gene_annotation"`` key.
        max_genes: Cap on genes submitted.

    Returns:
        A :class:`ToolOutcome` whose ``result`` is a ``GeneAnnotationResult``.
    """

    selected = list(genes or ())[:max_genes]
    outcome = ToolOutcome(
        tool=TOOL_GENE_ANNOTATION,
        input_summary=_genes_summary(selected),
    )

    if not selected:
        outcome.detail = "skipped — no genes"
        outcome.output_summary = "no input genes"
        return outcome

    try:
        from rag.gene_annotation import run_gene_annotation_retrieval

        merged = _with_override(config, "gene_annotation", {"max_genes": max_genes})
        result = run_gene_annotation_retrieval(selected, config=merged)
    except Exception as exc:  # noqa: BLE001 — a tool must never raise.
        outcome.status = STATUS_ERROR
        outcome.error = f"{type(exc).__name__}: {exc}"
        outcome.detail = "unavailable"
        outcome.output_summary = f"failed ({type(exc).__name__})"
        return outcome

    outcome.result = result
    rows = adapters.iter_annotations(result)
    missing = list(adapters.get_field(result, "missing_genes") or ())

    if rows:
        outcome.status = STATUS_OK
        source = adapters.get_field(result, "source_database") or "NCBI Gene"
        outcome.detail = f"{len(rows)} gene(s) · {source}"
        outcome.output_summary = f"annotated {len(rows)} gene(s)"
        if missing:
            outcome.output_summary += f"; {len(missing)} not found"
    else:
        annotation_status = adapters.status_of(result)
        if _is_failure_status_message(annotation_status):
            outcome.status = STATUS_ERROR
            outcome.error = annotation_status
            outcome.detail = "unavailable"
            outcome.output_summary = annotation_status
        else:
            outcome.status = STATUS_EMPTY
            outcome.detail = "no annotations found"
            outcome.output_summary = annotation_status or "no annotations returned"

    return outcome


# -- PubMed retrieval (T-015 upstream) ------------------


def run_pubmed_tool(
    genes: list[str],
    pathways: list[str] | None = None,
    question: str = "",
    max_results: int = DEFAULT_MAX_ABSTRACTS,
    disease: str = DEFAULT_DISEASE,
    timeout: float = DEFAULT_PUBMED_TIMEOUT,
    max_retries: int = DEFAULT_PUBMED_RETRIES,
    semantic_rerank: bool = False,
    config: object | None = None,
) -> ToolOutcome:
    """Retrieve PubMed abstracts for the ROI genes and pathways.

    Args:
        genes: Ranked gene symbols.
        pathways: Enriched pathway names, used to sharpen the query.
        question: The user's message. Used only for optional semantic
            re-ranking, never injected into the E-utilities query — free text
            in a PubMed query degrades recall badly.
        max_results: Maximum abstracts to return.
        disease: Disease anchor for the query.
        timeout: Per-request timeout passed to the NCBI client.
        max_retries: Retry budget passed to the NCBI client.
        semantic_rerank: Whether to re-rank the retrieved abstracts against the
            user's question with ChromaDB. Off by default: Chroma's default
            embedding function downloads a ~79 MB ONNX model on first use, and
            it swallows all errors, so a failure is indistinguishable from "no
            matches".
        config: Application config, currently unused by the PubMed module
            (which is configured through kwargs and ``PUBMED_*`` env vars).

    Returns:
        A :class:`ToolOutcome` whose ``result`` is a ``PubMedResult``.
    """

    outcome = ToolOutcome(tool=TOOL_PUBMED, input_summary=_genes_summary(genes))

    if not genes and not pathways:
        outcome.detail = "skipped — no genes"
        outcome.output_summary = "no input genes or pathways"
        return outcome

    try:
        from rag.pubmed_retrieval import (
            PubMedResult,
            build_pubmed_query,
            rerank_pubmed_result,
            search_pubmed,
        )

        query = build_pubmed_query(
            list(genes or ()),
            list(pathways or ()),
            disease=disease,
        )
        if not query:
            outcome.detail = "skipped — empty query"
            outcome.output_summary = "no query could be built"
            return outcome

        outcome.extras["query"] = query
        outcome.input_summary = f"{_genes_summary(genes)} · {disease}"

        candidate_pool = max(
            max_results,
            _int_config(
                config,
                "pubmed_retrieval",
                "candidate_pool",
                DEFAULT_PUBMED_CANDIDATE_POOL,
                minimum=max_results,
                maximum=30,
            ),
        )
        candidate_result = search_pubmed(
            query,
            max_results=candidate_pool,
            timeout=timeout,
            max_retries=max_retries,
        )
        result = rerank_pubmed_result(
            candidate_result,
            genes=genes,
            pathways=pathways or (),
            disease=disease,
            question=question,
            top_k=max_results,
        )

        outcome.extras["candidate_pool_requested"] = candidate_pool
        outcome.extras["candidate_count"] = len(adapters.iter_papers(candidate_result))
        outcome.extras["ranking"] = "deterministic_roi_evidence"

        # Optional embedding re-ranking is now a real ranking step rather than
        # trace-only metadata. It runs over the same current-call candidate
        # corpus and, when successful, replaces the deterministic order.
        if semantic_rerank and question and adapters.iter_papers(candidate_result):
            semantic_query = (
                f"{question}\nTissue context: {disease}.\n"
                f"ROI genes: {', '.join(list(genes or ())[:10])}.\n"
                f"ROI pathways: {', '.join(list(pathways or ())[:5])}."
            )
            semantic_rows = _semantic_rerank(
                candidate_result,
                semantic_query,
                max_results,
            )
            outcome.extras["semantic"] = semantic_rows
            semantic_pmids = [
                adapters.clean_text(row.get("pmid"))
                for row in semantic_rows
                if isinstance(row, dict)
            ]
            semantic_pmids = [pmid for pmid in semantic_pmids if pmid]
            if semantic_pmids:
                by_pmid = {
                    adapters.clean_text(adapters.get_field(paper, "pmid")): paper
                    for paper in adapters.iter_papers(candidate_result)
                }
                selected = [by_pmid[pmid] for pmid in semantic_pmids if pmid in by_pmid]
                if selected:
                    result = PubMedResult(
                        papers=selected[:max_results],
                        query=query,
                        status_message=(
                            f"Retrieved {len(adapters.iter_papers(candidate_result))} "
                            f"PubMed candidate(s); retained {len(selected[:max_results])} "
                            "semantically ROI-focused paper(s)."
                        ),
                    )
                    outcome.extras["ranking"] = "semantic_roi_evidence"
    except Exception as exc:  # noqa: BLE001 — a tool must never raise.
        outcome.status = STATUS_ERROR
        outcome.error = f"{type(exc).__name__}: {exc}"
        outcome.detail = "unavailable"
        outcome.output_summary = f"failed ({type(exc).__name__})"
        return outcome

    outcome.result = result
    papers = adapters.iter_papers(result)

    if papers:
        outcome.status = STATUS_OK
        pmids = [adapters.clean_text(adapters.get_field(p, "pmid")) for p in papers]
        pmids = [pmid for pmid in pmids if pmid]
        # Expose the disease anchor and PMIDs so retrieval can be audited in the trace.
        outcome.detail = f"{disease} · {len(papers)} abstract(s)" + (
            f" · PMID {', '.join(pmids[:3])}" if pmids else ""
        )
        outcome.output_summary = f"retrieved {len(papers)} abstract(s)"

    else:
        pubmed_status = adapters.status_of(result)
        if _is_failure_status_message(pubmed_status):
            outcome.status = STATUS_ERROR
            outcome.error = pubmed_status
            outcome.detail = "unavailable"
            outcome.output_summary = pubmed_status
        else:
            outcome.status = STATUS_EMPTY
            outcome.detail = "no matching papers"
            outcome.output_summary = pubmed_status or "no papers returned"

    return outcome


def _semantic_rerank(pubmed_result: object, question: str, top_k: int) -> list[dict]:
    """Re-rank retrieved abstracts against the question (T-018 integration).

    Returns ``[]`` on any failure. Note that ``semantic_search_abstracts``
    expects the *envelope* — handing it ``result.papers`` returns ``[]``
    silently because it looks for a ``papers`` attribute.
    """

    try:
        from rag.pubmed_retrieval import semantic_search_abstracts

        return semantic_search_abstracts(pubmed_result, question, top_k=top_k)
    except Exception:  # noqa: BLE001 — ranking is a nicety, never fatal.
        return []


def _with_override(
    config: object | None,
    section: str,
    overrides: dict[str, Any],
) -> dict[str, Any] | None:
    """Return ``config`` with ``overrides`` merged into ``config[section]``.

    Leaves the caller's dict untouched. Returns a config containing only the
    overrides when ``config`` is ``None`` or is not a dict, so a caller passing
    an attribute-style config object still gets its own values honoured by
    upstream's dotted-key resolver — we simply do not try to merge into it.
    """

    if not isinstance(config, dict):
        return {section: dict(overrides)} if overrides else config

    merged = dict(config)
    existing = merged.get(section)
    section_values = dict(existing) if isinstance(existing, dict) else {}
    # Function arguments are the immediate call contract and therefore take
    # precedence over a broader application default. The old setdefault()
    # meant run_pathway_tool(top_n=6) silently returned config top_n=10.
    for key, value in overrides.items():
        section_values[key] = value
    merged[section] = section_values
    return merged


def _int_config(
    config: object | None,
    section: str,
    key: str,
    default: int,
    *,
    minimum: int,
    maximum: int,
) -> int:
    """Read one bounded integer from a dict config without ever raising."""

    value: Any = default
    if isinstance(config, dict):
        block = config.get(section)
        if isinstance(block, dict) and key in block:
            value = block.get(key)
        elif key in config:
            value = config.get(key)
    try:
        parsed = int(value)
    except (TypeError, ValueError, OverflowError):
        parsed = default
    return min(max(parsed, minimum), maximum)


# Wrappers expose schemas for LangChain and future tool-calling models.


def _pathway_tool_fn(genes: list[str]) -> dict:
    """Run GO and KEGG pathway enrichment for a list of gene symbols.

    Args:
        genes: Gene symbols from the selected region's DEG result.
    """

    outcome = run_pathway_tool(genes)
    result = outcome.result
    return result.to_dict() if hasattr(result, "to_dict") else {"pathways": []}


def _gene_annotation_tool_fn(genes: list[str]) -> dict:
    """Retrieve NCBI Gene annotations describing what each gene does.

    Args:
        genes: Gene symbols from the selected region's DEG result.
    """

    outcome = run_gene_annotation_tool(genes)
    result = outcome.result
    return result.to_dict() if hasattr(result, "to_dict") else {"genes": []}


def _pubmed_tool_fn(genes: list[str], pathways: list[str] | None = None) -> dict:
    """Retrieve PubMed abstracts supporting the region's genes and pathways.

    Args:
        genes: Gene symbols from the selected region's DEG result.
        pathways: Enriched pathway names used to sharpen the query.
    """

    outcome = run_pubmed_tool(genes, pathways or [])
    result = outcome.result
    return result.to_dict() if hasattr(result, "to_dict") else {"papers": []}


class _FallbackTool:
    """Minimal stand-in used when ``langchain_core`` is unavailable.

    Mirrors the slice of the ``StructuredTool`` surface this project uses, so
    the agent keeps working — and keeps being testable — in an environment
    without LangChain installed.
    """

    __slots__ = ("name", "description", "func")

    def __init__(self, name: str, func: Any) -> None:
        self.name = name
        self.func = func
        self.description = (func.__doc__ or "").strip().splitlines()[0] if func.__doc__ else name

    def invoke(self, args: dict | None = None) -> Any:
        """Call the underlying function with ``args`` as keyword arguments."""

        return self.func(**(args or {}))

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        return f"<_FallbackTool {self.name!r}>"


def _build_tool(name: str, func: Any) -> Any:
    """Wrap ``func`` as a LangChain ``StructuredTool``, or fall back."""

    try:
        from langchain_core.tools import StructuredTool

        return StructuredTool.from_function(func=func, name=name, parse_docstring=True)
    except Exception:  # noqa: BLE001 — LangChain is optional at runtime.
        return _FallbackTool(name, func)


pathway_tool = _build_tool(TOOL_PATHWAY, _pathway_tool_fn)
gene_annotation_tool = _build_tool(TOOL_GENE_ANNOTATION, _gene_annotation_tool_fn)
pubmed_tool = _build_tool(TOOL_PUBMED, _pubmed_tool_fn)

#: Every tool the agent can call, keyed by name.
TOOLS = {
    TOOL_GENE_ANNOTATION: gene_annotation_tool,
    TOOL_PATHWAY: pathway_tool,
    TOOL_PUBMED: pubmed_tool,
}


__all__ = [
    "DEFAULT_DISEASE",
    "DEFAULT_MAX_ABSTRACTS",
    "DEFAULT_MAX_ANNOTATED_GENES",
    "DEFAULT_PUBMED_CANDIDATE_POOL",
    "DEFAULT_PUBMED_RETRIES",
    "DEFAULT_PUBMED_TIMEOUT",
    "DEFAULT_TOP_PATHWAYS",
    "TOOLS",
    "ToolOutcome",
    "gene_annotation_tool",
    "pathway_tool",
    "pubmed_tool",
    "run_gene_annotation_tool",
    "run_pathway_tool",
    "run_pubmed_tool",
]
