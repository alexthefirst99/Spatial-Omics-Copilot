"""Deprecated shim — agent tools now live in :mod:`rag.copilot_agent.tools`.

T-020 is implemented there, with three tools rather than the two originally
sketched here: ``gene_annotation_tool`` was added alongside ``pathway_tool``
and ``pubmed_tool`` when T-049 introduced gene annotation retrieval.

DEG extraction is still not a tool — it runs in ``app/app.py`` when the user
selects a cluster or draws an ROI, before any chat message exists.
"""

from rag.copilot_agent.tools import (
    gene_annotation_tool,
    pathway_tool,
    pubmed_tool,
)

__all__ = ["gene_annotation_tool", "pathway_tool", "pubmed_tool"]
