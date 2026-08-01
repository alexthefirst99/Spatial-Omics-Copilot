"""Copilot agent — public API (Person 5).

Per ``docs/rules.md`` section 4, callers import from this ``__init__`` only,
never from the implementation modules directly.

``run_agent`` is the entry point ``app/routes.py`` calls and is contract-frozen
by ``docs/specs.md`` section 3.4. ``run_copilot_agent`` is the richer
plan-level entry point used by the integration pipeline; it returns an
``AgentResult`` and accepts pre-computed evidence.

The legacy package ``rag.agent`` re-exports ``run_agent`` from here, so no
caller outside this package needs to change.
"""

from rag.copilot_agent.graph import (
    MAX_TOOL_CALLS,
    run_agent,
    run_copilot_agent,
)
from rag.copilot_agent.llm import (
    LLMResponse,
    call_deepinfra_chat,
    call_deepinfra_model,
    is_configured,
)
from rag.copilot_agent.models import (
    AgentResult,
    Citation,
    DegBar,
    PathwayBar,
    TraceStep,
)
from rag.copilot_agent.multimodal import (
    build_multimodal_prompt_payload,
    model_supports_vision,
)
from rag.copilot_agent.prompt import (
    build_evidence_context,
    build_no_data_context,
    build_prompt_context,
)
from rag.copilot_agent.routing import (
    ToolPlan,
    classify_question,
    plan_tools,
)
from rag.copilot_agent.tools import (
    ToolOutcome,
    gene_annotation_tool,
    pathway_tool,
    pubmed_tool,
    run_gene_annotation_tool,
    run_pathway_tool,
    run_pubmed_tool,
)

__all__ = [
    "MAX_TOOL_CALLS",
    "AgentResult",
    "Citation",
    "DegBar",
    "LLMResponse",
    "PathwayBar",
    "ToolOutcome",
    "ToolPlan",
    "TraceStep",
    "build_evidence_context",
    "build_multimodal_prompt_payload",
    "build_no_data_context",
    "build_prompt_context",
    "call_deepinfra_chat",
    "call_deepinfra_model",
    "classify_question",
    "is_configured",
    "model_supports_vision",
    "gene_annotation_tool",
    "pathway_tool",
    "plan_tools",
    "pubmed_tool",
    "run_agent",
    "run_copilot_agent",
    "run_gene_annotation_tool",
    "run_pathway_tool",
    "run_pubmed_tool",
]
