"""Deprecated shim — the LangGraph agent now lives in :mod:`rag.copilot_agent`.

This module used to hold the mock ``run_agent`` that delegated to
``rag.pipeline._run_sequential`` (which always ran pathway enrichment and
PubMed retrieval regardless of the question). T-021 replaced it with a real
LangGraph state machine in ``rag.copilot_agent.graph``.

The name is kept because ``docs/tech.md`` documents this path and because
``rag.agent.__init__`` re-exports from it. ``run_agent``'s signature and return
shape are unchanged — see ``docs/specs.md`` section 3.4.
"""

from __future__ import annotations

from rag.copilot_agent.graph import run_agent, run_copilot_agent

__all__ = ["run_agent", "run_copilot_agent"]
