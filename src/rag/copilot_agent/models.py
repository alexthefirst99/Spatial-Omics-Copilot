"""Result models for the copilot agent.

Thin re-export shim. ``AgentResult`` (with ``TraceStep``/``Citation``/
``PathwayBar``/``DegBar``) now lives in ``rag.contracts`` (Person 6's shared
contracts module); this module keeps the ``rag.copilot_agent.models`` import
path stable for ``graph.py``, ``tools.py``, ``adapters.py`` and the package
``__init__.py``.

Two output shapes matter, both still on ``AgentResult`` itself:

``to_legacy_dict()``
    The exact ``{gene_objects, context_str, metadata}`` dict that
    ``app/routes.py`` reads and ``app/assets/chat.js`` renders. This is a hard
    contract — see ``docs/specs.md`` section 3.4.

``to_dict()``
    The richer plan-level view (``answer``, ``trace``, ``citations``,
    ``used_roi_image``) that ``run_copilot_agent`` returns for the integration
    pipeline.
"""

from __future__ import annotations

from rag.contracts import (
    ICON_AGENT,
    ICON_DEG,
    ICON_GENE,
    ICON_IMAGE,
    ICON_PATHWAY,
    ICON_PUBMED,
    STATUS_EMPTY,
    STATUS_ERROR,
    STATUS_OK,
    STATUS_PENDING,
    STATUS_SKIPPED,
    AgentResult,
    Citation,
    DegBar,
    PathwayBar,
    TraceStep,
)

__all__ = [
    "ICON_AGENT",
    "ICON_DEG",
    "ICON_GENE",
    "ICON_IMAGE",
    "ICON_PATHWAY",
    "ICON_PUBMED",
    "STATUS_EMPTY",
    "STATUS_ERROR",
    "STATUS_OK",
    "STATUS_PENDING",
    "STATUS_SKIPPED",
    "AgentResult",
    "Citation",
    "DegBar",
    "PathwayBar",
    "TraceStep",
]
