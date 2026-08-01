"""Backward-compatible entry point for the copilot agent.

The implementation moved to :mod:`rag.copilot_agent`, which is Person 5's
folder in the task plan. This package remains the import path documented in
``docs/tech.md`` and used by ``app/routes.py``:

    from rag.agent import run_agent

Both names refer to the same function, so no caller outside the RAG layer needs
to change. New code should import from :mod:`rag.copilot_agent`.
"""

from rag.copilot_agent import run_agent, run_copilot_agent

__all__ = ["run_agent", "run_copilot_agent"]
