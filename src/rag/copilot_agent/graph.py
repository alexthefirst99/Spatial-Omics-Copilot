"""LangGraph agent for the spatial omics copilot (T-021, T-022, T-023).

Graph shape::

    START ─▶ route ─┬─▶ run_tool ──┐
                    │      ▲       │
                    │      └───────┘  (loop while work remains and the
                    │                  tool budget is not spent)
                    └─▶ synthesize ─▶ END

``route`` classifies the question and produces a work queue; ``run_tool``
executes one tool per superstep and records what it did; ``synthesize``
assembles the evidence block. Executing one tool per superstep — rather than
all of them in a single node — is what makes the max-iteration guard (T-023)
meaningful and what makes the trace reflect real progress.

Two entry points share this machinery:

:func:`run_agent`
    The legacy contract ``app/routes.py`` depends on. Returns the
    ``{gene_objects, context_str, metadata}`` dict documented in
    ``docs/specs.md`` section 3.4. This signature must not change.

:func:`run_copilot_agent`
    The plan-level entry point, returning an :class:`AgentResult`. It accepts
    pre-computed annotation / pathway / PubMed results — which is how the
    integration pipeline in the task plan calls it — and only invokes a tool
    itself when the corresponding result was not supplied. That reconciles the
    plan's fixed pipeline with T-021's requirement that the agent choose its
    own tools.

LangGraph is optional at runtime. When it is absent the same node functions run
sequentially in :func:`_run_without_langgraph`, so behaviour and trace output
are identical — only the orchestration differs.
"""

from __future__ import annotations

import operator
from typing import Annotated, Any, TypedDict

from rag.copilot_agent import adapters, tools
from rag.copilot_agent.models import (
    ICON_AGENT,
    ICON_DEG,
    ICON_GENE,
    ICON_IMAGE,
    ICON_PATHWAY,
    ICON_PUBMED,
    STATUS_EMPTY,
    STATUS_ERROR,
    STATUS_OK,
    STATUS_SKIPPED,
    AgentResult,
    TraceStep,
)
from rag.copilot_agent.prompt import build_evidence_context, build_no_data_context
from rag.copilot_agent.routing import (
    INTENT_IMAGE,
    TOOL_GENE_ANNOTATION,
    TOOL_PATHWAY,
    TOOL_PUBMED,
    plan_tools,
)

#: Hard cap on tool calls per turn (``docs/rules.md`` section 4).
MAX_TOOL_CALLS = 5

#: Extra LangGraph supersteps beyond the tool calls: route, synthesize, and the
#: terminal step. LangGraph's recursion limit is off by one from intuition —
#: the minimum workable value is (number of supersteps + 1) — so this slack is
#: required, not cosmetic.
_RECURSION_SLACK = 4

_ICONS = {
    TOOL_PATHWAY: ICON_PATHWAY,
    TOOL_PUBMED: ICON_PUBMED,
    TOOL_GENE_ANNOTATION: ICON_GENE,
}

_TOOL_LABELS = {
    TOOL_PATHWAY: "Pathway enrichment",
    TOOL_PUBMED: "PubMed retrieval",
    TOOL_GENE_ANNOTATION: "Gene annotation",
}

# Cached compiled graph. Compilation re-runs validation, so it is done once and
# reused; the import of langgraph itself costs ~0.3s and is deferred to first
# use so importing this module stays cheap for the web layer.
_COMPILED_GRAPH: Any = None
_LANGGRAPH_AVAILABLE: bool | None = None


class AgentState(TypedDict, total=False):
    """State threaded through the graph.

    Only ``trace`` uses a reducer. Every other key is written by at most one
    node per superstep, so last-write-wins is correct for them. Nodes must read
    with ``.get()`` — LangGraph does not enforce TypedDict required-ness, and a
    key absent from the initial input is simply missing from the state dict.
    """

    # Inputs.
    question: str
    label: str
    gene_objects: list[dict]
    genes: list[str]
    config: dict
    roi: Any
    roi_image: Any
    image_attached: bool | None
    max_tool_calls: int
    semantic_rerank: bool
    disease: str
    # Pre-computed results supplied by the integration pipeline.
    supplied: dict

    # Working state.
    pending: list[str]
    tool_calls: int
    outcomes: dict
    intent: str
    plan_reason: str
    uses_roi_image: bool
    guard_tripped: bool

    # Output.
    context_str: str
    trace: Annotated[list, operator.add]


# --- Nodes ---------------------------------------------------------------


def route_node(state: AgentState) -> dict:
    """Classify the question and build the tool work queue (T-021)."""

    question = state.get("question", "") or ""
    genes = state.get("genes", []) or []
    max_calls = state.get("max_tool_calls", MAX_TOOL_CALLS)

    plan = plan_tools(
        question,
        genes=genes,
        has_genes=bool(genes),
        has_roi_image=bool(state.get("image_attached")),
    )

    # Anything the caller already computed is not re-fetched. This is what lets
    # the integration pipeline pass results in while the agent still decides
    # what is needed.
    supplied = state.get("supplied") or {}
    wanted = [name for name in plan.tools if supplied.get(name) is None]

    # Apply the per-turn budget here, where both the plan and the already-known
    # results are visible, and report what it cost (T-023).
    pending = wanted[:max_calls]
    dropped = wanted[max_calls:]

    trace: list[TraceStep] = []

    if genes:
        trace.append(
            TraceStep(
                step="Loaded region gene expression",
                detail=f"{len(genes)} genes · {state.get('label', 'selection')}",
                icon=ICON_DEG,
                tool="",
                status=STATUS_OK,
                input_summary=state.get("label", "selection"),
                output_summary=f"{len(genes)} differentially expressed genes",
            )
        )
    else:
        trace.append(
            TraceStep(
                step="No gene expression data loaded",
                detail="Select an ROI or cluster to gather context",
                icon=ICON_DEG,
                tool="",
                status=STATUS_EMPTY,
                input_summary=state.get("label", "selection"),
                output_summary="no genes available",
            )
        )

    planned = ", ".join(_TOOL_LABELS.get(name, name) for name in plan.tools)
    trace.append(
        TraceStep(
            step=f"Routed question as: {plan.intent.replace('_', ' ')}",
            detail=planned or "no tools needed",
            icon=ICON_AGENT,
            tool="",
            status=STATUS_OK,
            input_summary=question[:120],
            output_summary=plan.reason,
        )
    )

    if dropped:
        trace.append(
            TraceStep(
                step="Tool budget reached",
                detail=f"limit {max_calls} per turn",
                icon=ICON_AGENT,
                tool="",
                status=STATUS_SKIPPED,
                input_summary=", ".join(dropped),
                output_summary=(
                    f"skipped {', '.join(_TOOL_LABELS.get(n, n) for n in dropped)} "
                    "to bound the turn"
                ),
            )
        )

    if plan.intent == INTENT_IMAGE and state.get("image_attached"):
        trace.append(
            TraceStep(
                step="Using cropped ROI image",
                detail="tissue appearance question",
                icon=ICON_IMAGE,
                tool="",
                status=STATUS_OK,
                input_summary="cropped ROI image",
                output_summary="image included in model input",
            )
        )

    return {
        "pending": pending,
        "tool_calls": 0,
        "outcomes": dict(supplied),
        "intent": plan.intent,
        "plan_reason": plan.reason,
        "uses_roi_image": plan.uses_roi_image,
        "guard_tripped": False,
        "trace": trace,
    }


def run_tool_node(state: AgentState) -> dict:
    """Execute exactly one queued tool and record the outcome (T-022)."""

    pending = list(state.get("pending") or [])
    if not pending:
        return {}

    name = pending.pop(0)
    genes = state.get("genes", []) or []
    config = state.get("config")
    outcomes = dict(state.get("outcomes") or {})

    if name == TOOL_PATHWAY:
        outcome = tools.run_pathway_tool(genes, config=config)
    elif name == TOOL_GENE_ANNOTATION:
        outcome = tools.run_gene_annotation_tool(genes, config=config)
    elif name == TOOL_PUBMED:
        # Pathway names sharpen the PubMed query, so use them when the pathway
        # tool has already run this turn.
        pathway_outcome = outcomes.get(TOOL_PATHWAY)
        pathway_result = getattr(pathway_outcome, "result", pathway_outcome)
        outcome = tools.run_pubmed_tool(
            genes,
            pathways=adapters.pathway_names(pathway_result),
            question=state.get("question", ""),
            disease=state.get("disease") or tools.DEFAULT_DISEASE,
            semantic_rerank=bool(state.get("semantic_rerank")),
            config=config,
        )
    else:  # pragma: no cover - unreachable while the router owns the queue.
        return {"pending": pending}

    outcomes[name] = outcome

    return {
        "pending": pending,
        "tool_calls": state.get("tool_calls", 0) + 1,
        "outcomes": outcomes,
        "trace": [_trace_for(outcome)],
    }


def _trace_for(outcome: tools.ToolOutcome) -> TraceStep:
    """Render one tool outcome as a trace row."""

    label = _TOOL_LABELS.get(outcome.tool, outcome.tool)
    if outcome.status == STATUS_ERROR:
        step = f"{label} unavailable"
    elif outcome.status == STATUS_EMPTY:
        step = f"{label} returned no results"
    else:
        step = label

    return TraceStep(
        step=step,
        detail=outcome.detail,
        icon=_ICONS.get(outcome.tool, ICON_AGENT),
        tool=outcome.tool,
        status=outcome.status,
        input_summary=outcome.input_summary,
        output_summary=outcome.output_summary or outcome.error,
    )


def synthesize_node(state: AgentState) -> dict:
    """Assemble the evidence block from whatever the tools produced."""

    outcomes = state.get("outcomes") or {}
    gene_objects = state.get("gene_objects") or []
    label = state.get("label", "selection")

    trace: list[TraceStep] = []

    # The guard trips when the router queued more work than the budget allows.
    pending = state.get("pending") or []
    if pending:
        trace.append(
            TraceStep(
                step="Tool budget reached",
                detail=f"stopped after {state.get('tool_calls', 0)} tool calls",
                icon=ICON_AGENT,
                tool="",
                status=STATUS_SKIPPED,
                input_summary=", ".join(pending),
                output_summary="remaining tools skipped to bound the turn",
            )
        )

    if not gene_objects:
        return {
            "context_str": build_no_data_context(label),
            "guard_tripped": bool(pending),
            "trace": trace,
        }

    gaps = _evidence_gaps(outcomes)
    context = build_evidence_context(
        label=label,
        gene_objects=gene_objects,
        annotation_result=_result_of(outcomes.get(TOOL_GENE_ANNOTATION)),
        pathway_result=_result_of(outcomes.get(TOOL_PATHWAY)),
        pubmed_result=_result_of(outcomes.get(TOOL_PUBMED)),
        roi=state.get("roi"),
        roi_image=state.get("roi_image"),
        image_attached=state.get("image_attached"),
        evidence_gaps=gaps,
    )

    return {
        "context_str": context,
        "guard_tripped": bool(pending),
        "trace": trace,
    }


def _result_of(outcome: Any) -> Any:
    """Unwrap a :class:`ToolOutcome`, or pass a pre-supplied result through."""

    if outcome is None:
        return None
    return getattr(outcome, "result", outcome)


def _evidence_gaps(outcomes: dict) -> list[str]:
    """Describe tools that ran but produced nothing.

    Naming the gap explicitly is what lets the model say "no literature was
    retrieved" instead of quietly inventing a citation.
    """

    gaps: list[str] = []
    for name, outcome in outcomes.items():
        status = getattr(outcome, "status", None)
        if status == STATUS_ERROR:
            gaps.append(f"{_TOOL_LABELS.get(name, name)} was unavailable this turn.")
        elif status == STATUS_EMPTY:
            summary = getattr(outcome, "output_summary", "") or "returned no results"
            gaps.append(f"{_TOOL_LABELS.get(name, name)}: {summary}.")
    return gaps


def _should_continue(state: AgentState) -> str:
    """Route after a tool runs: another tool, or synthesis (T-023).

    Returns a node name directly rather than a path_map key — a router value
    missing from a path_map raises a bare ``KeyError`` with no graph context.
    """

    pending = state.get("pending") or []
    used = state.get("tool_calls", 0)
    budget = state.get("max_tool_calls", MAX_TOOL_CALLS)
    return "run_tool" if pending and used < budget else "synthesize"


# --- Graph assembly ------------------------------------------------------


def _build_graph() -> Any:
    """Compile the LangGraph state machine, or return ``None`` if unavailable."""

    global _COMPILED_GRAPH, _LANGGRAPH_AVAILABLE

    if _COMPILED_GRAPH is not None:
        return _COMPILED_GRAPH
    if _LANGGRAPH_AVAILABLE is False:
        return None

    try:
        from langgraph.graph import END, START, StateGraph
    except ImportError:
        _LANGGRAPH_AVAILABLE = False
        return None

    builder = StateGraph(AgentState)
    builder.add_node("route", route_node)
    builder.add_node("run_tool", run_tool_node)
    builder.add_node("synthesize", synthesize_node)

    builder.add_edge(START, "route")
    builder.add_conditional_edges("route", _should_continue)
    builder.add_conditional_edges("run_tool", _should_continue)
    builder.add_edge("synthesize", END)

    _COMPILED_GRAPH = builder.compile()
    _LANGGRAPH_AVAILABLE = True
    return _COMPILED_GRAPH


def _run_without_langgraph(state: dict) -> dict:
    """Run the same nodes sequentially when LangGraph is not installed.

    Applies the ``trace`` reducer by hand so the output is identical to the
    graph path.
    """

    merged = dict(state)
    merged.setdefault("trace", [])

    def apply(delta: dict) -> None:
        for key, value in (delta or {}).items():
            if key == "trace":
                merged["trace"] = list(merged.get("trace", [])) + list(value)
            else:
                merged[key] = value

    apply(route_node(merged))
    while _should_continue(merged) == "run_tool":
        apply(run_tool_node(merged))
    apply(synthesize_node(merged))
    return merged


def _invoke(state: dict, max_tool_calls: int) -> dict:
    """Run the agent, preferring LangGraph and falling back to plain Python."""

    graph = _build_graph()
    if graph is None:
        return _run_without_langgraph(state)

    from langgraph.errors import GraphRecursionError

    try:
        return graph.invoke(
            state,
            config={"recursion_limit": max_tool_calls + _RECURSION_SLACK},
        )
    except GraphRecursionError:
        # A genuine backstop: _should_continue already enforces the budget, so
        # reaching this means the graph itself misbehaved. Degrade rather than
        # lose the user's turn.
        return _run_without_langgraph(state)


# --- Public entry points -------------------------------------------------


def run_copilot_agent(
    question: str = "",
    roi: Any = None,
    roi_image: Any = None,
    deg: Any = None,
    gene_annotations: Any = None,
    pathways: Any = None,
    pubmed: Any = None,
    config: dict | None = None,
    *,
    label: str = "selection",
    image_attached: bool | None = None,
    max_tool_calls: int = MAX_TOOL_CALLS,
    semantic_rerank: bool = False,
    disease: str | None = None,
    synthesize_answer: bool = False,
) -> AgentResult:
    """Run one agent turn and return a structured result.

    Any of ``gene_annotations`` / ``pathways`` / ``pubmed`` that is supplied is
    used as-is; anything left as ``None`` is fetched by the agent itself, and
    only when the routed question actually calls for it.

    Args:
        question: The researcher's message.
        roi: ``ROISelection``-like object describing the selected region.
        roi_image: ``ROIImageResult``-like object for the cropped H&E image.
        deg: DEG payload in any supported shape — a ``DEGResult``, a legacy
            result dict, or a bare list of gene rows.
        gene_annotations: Pre-computed ``GeneAnnotationResult``, or ``None``.
        pathways: Pre-computed ``PathwayResult``, or ``None``.
        pubmed: Pre-computed ``PubMedResult``, or ``None``.
        config: Application config, forwarded to the tools.
        label: Region label for UI headers.
        image_attached: Whether the cropped image is genuinely reaching the
            model. Defaults to whether ``roi_image`` carries a crop path —
            claims about tissue appearance are only permitted when True.
        max_tool_calls: Per-turn tool budget (T-023).
        semantic_rerank: Enable ChromaDB re-ranking of retrieved abstracts.
        disease: Disease anchor for the PubMed query.
        synthesize_answer: Call DeepInfra to fill ``AgentResult.answer``
            (T-047). Off by default: in the running app the LLM call belongs to
            ``app/worker.py``, which streams it (``docs/rules.md`` section 3).
            The integration pipeline turns this on when it wants a complete
            answer rather than an evidence block.

    Returns:
        An :class:`AgentResult`. Never raises for ordinary failures — missing
        data and failed tools are reported through the trace and status.
    """

    gene_objects = adapters.normalize_gene_objects(deg)
    genes = adapters.extract_gene_symbols(gene_objects)

    if image_attached is None and roi_image is not None:
        # A roi_image was supplied, so its crop path is authoritative.
        image_attached = bool(adapters.get_field(roi_image, "crop_path"))
    # Otherwise image_attached stays None — "unknown". run_agent cannot know:
    # its signature carries no image, yet app/worker.py may still attach the
    # crop to the same message when a vision model is selected.

    supplied = {
        TOOL_GENE_ANNOTATION: gene_annotations,
        TOOL_PATHWAY: pathways,
        TOOL_PUBMED: pubmed,
    }

    state: dict = {
        "question": question or "",
        "label": label or "selection",
        "gene_objects": gene_objects,
        "genes": genes,
        "config": config or {},
        "roi": roi,
        "roi_image": roi_image,
        "image_attached": image_attached,
        "max_tool_calls": max_tool_calls,
        "semantic_rerank": bool(semantic_rerank),
        "disease": disease or tools.DEFAULT_DISEASE,
        "supplied": {k: v for k, v in supplied.items() if v is not None},
        "pending": [],
        "tool_calls": 0,
        "outcomes": {},
        "trace": [],
    }

    final = _invoke(state, max_tool_calls)
    result = _to_result(final)

    if synthesize_answer:
        _attach_llm_answer(result, final, config or {})

    return result


def _attach_llm_answer(result: AgentResult, state: dict, config: dict) -> None:
    """Fill ``result.answer`` from DeepInfra, recording the attempt (T-047).

    Mutates ``result`` in place. A failure is recorded in the trace and leaves
    the evidence block intact, so the caller still has something to show.
    """

    from rag.copilot_agent.llm import call_deepinfra_chat, is_configured
    from rag.copilot_agent.multimodal import build_multimodal_prompt_payload

    if not is_configured(config):
        result.trace.append(
            TraceStep(
                step="Answer synthesis skipped",
                detail="DeepInfra not configured",
                icon=ICON_AGENT,
                tool="",
                status=STATUS_SKIPPED,
                input_summary="deepinfra",
                output_summary="set DEEPINFRA_API_KEY and deepinfra.model to enable",
            )
        )
        return

    outcomes = state.get("outcomes") or {}
    payload = build_multimodal_prompt_payload(
        state.get("question", ""),
        roi_image=state.get("roi_image"),
        deg=state.get("gene_objects"),
        gene_annotations=_result_of(outcomes.get(TOOL_GENE_ANNOTATION)),
        pathways=_result_of(outcomes.get(TOOL_PATHWAY)),
        pubmed=_result_of(outcomes.get(TOOL_PUBMED)),
        config=config,
        label=state.get("label", "selection"),
        roi=state.get("roi"),
        evidence_gaps=tuple(_evidence_gaps(outcomes)),
    )

    response = call_deepinfra_chat(payload, config)
    result.answer = response.text
    # The image is only genuinely "used" if it reached the model.
    result.used_roi_image = bool(payload.get("image_included"))

    result.trace.append(
        TraceStep(
            step="Answer synthesised" if response.ok else "Answer synthesis failed",
            detail=response.model,
            icon=ICON_AGENT,
            tool="deepinfra",
            status=STATUS_OK if response.ok else STATUS_ERROR,
            input_summary=(
                "prompt with image" if payload.get("image_included") else "text prompt"
            ),
            output_summary=response.status_message or f"{len(response.text)} characters",
        )
    )


def _to_result(state: dict) -> AgentResult:
    """Convert final graph state into an :class:`AgentResult`."""

    outcomes = state.get("outcomes") or {}
    gene_objects = state.get("gene_objects") or []

    pathway_result = _result_of(outcomes.get(TOOL_PATHWAY))
    pubmed_result = _result_of(outcomes.get(TOOL_PUBMED))

    tools_called = [
        name
        for name, outcome in outcomes.items()
        if getattr(outcome, "status", None) is not None
    ]

    status_message = ""
    if not gene_objects:
        status_message = "No gene expression data loaded."

    return AgentResult(
        answer="",
        trace=list(state.get("trace") or []),
        citations=adapters.build_citations(pubmed_result),
        used_roi_image=bool(state.get("image_attached")) and bool(state.get("uses_roi_image")),
        context_str=state.get("context_str", ""),
        gene_objects=gene_objects,
        degs=adapters.build_deg_bars(gene_objects),
        pathways=adapters.build_pathway_bars(pathway_result),
        label=state.get("label", "selection"),
        intent=state.get("intent", ""),
        tools_called=tools_called,
        status_message=status_message,
    )


def run_agent(
    gene_objects: list,
    message: str = "",
    label: str = "selection",
) -> dict:
    """Run one agent turn for the chat UI.

    This is the contract ``app/routes.py`` calls and ``app/assets/chat.js``
    renders; the signature and return shape are fixed by ``docs/specs.md``
    section 3.4 and must not change.

    Unlike the previous implementation, an empty ``gene_objects`` no longer
    substitutes demo genes — T-044 requires the no-data case to say so plainly
    rather than present unrelated markers as if they were this region's.

    Args:
        gene_objects: DEG rows already computed by the UI on selection.
        message: The user's chat message; drives tool selection.
        label: Region label such as ``"Cluster 5"`` or ``"ROI"``.

    Returns:
        ``{"gene_objects": [...], "context_str": str, "metadata": {...}}``.
    """

    result = run_copilot_agent(
        question=message,
        deg=gene_objects,
        label=label,
    )
    return result.to_legacy_dict()


__all__ = [
    "MAX_TOOL_CALLS",
    "AgentState",
    "route_node",
    "run_agent",
    "run_copilot_agent",
    "run_tool_node",
    "synthesize_node",
]
