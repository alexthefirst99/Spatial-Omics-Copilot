"""
LangGraph Agent
===============
CURRENTLY MOCK — run_agent() just calls the fixed sequential pipeline
(DEG → Pathway → PubMed → context) regardless of the user question.
Replace run_agent() with a real LangGraph agent that decides dynamically
which tools to call based on the message.

Tools to wire up (defined in tools.py):
    deg_tool      — calls rag.deg.get_cluster/roi_high_expression_genes
    pathway_tool  — calls rag.pathway.enrich_pathways
    pubmed_tool   — calls rag.pubmed.retrieve_abstracts

Input:
    message    : str           — user chat message
    work_dir   : str           — session working directory
    cluster_id : str | None    — selected cluster label (or None)
    coords     : list | None   — ROI polygon coordinates (or None)
    folder_id  : str           — user folder ID (default "")

Expected output — dict:
    {
        "gene_objects": [
            {"gene": "SNAP25", "log2_fold_change": 3.81},
            ...
        ],
        "context_str": "

RAG-retrieved biological context...",
                        # formatted string injected into the LLM prompt
        "metadata": {
            "trace": [
                {"step": "Called DEG tool",     "detail": "25 genes · Cluster 2", "icon": "deg"},
                {"step": "Called Pathway tool", "detail": "Reactome · GO · KEGG",  "icon": "pathway"},
                ...
            ],              # DYNAMIC — only steps the agent actually ran
            "degs":      [{"gene": "SNAP25", "log2fc": 3.81}, ...],
            "pathways":  [{"source": "GO", "name": "...", "neg_log10p": 5.1, "gene_count": 8}, ...],
            "citations": [{"id": 1, "pmid": "38912204", "title": "...", "journal": "...", "year": 2024}, ...],
            "label":     "Cluster 2",
        }
    }

DO NOT CHANGE: routes.py, worker.py, app.py — they call run_agent() and
expect exactly this output format.
"""

from __future__ import annotations
from typing import Optional


# =======================================================================
# MOCK — replace everything below with real LangGraph implementation
# =======================================================================

def run_agent(
    work_dir: str,
    message: str = "",      # user question — real agent routes based on this
    cluster_id: Optional[str] = None,
    coords: Optional[list] = None,
    folder_id: str = "",
) -> dict:
    """
    MOCK: falls back to fixed sequential pipeline until LangGraph is implemented.
    Replace this function body with the real LangGraph agent (see instructions above).
    """
    from rag.pipeline import _run_sequential
    return _run_sequential(work_dir, cluster_id=cluster_id, coords=coords, folder_id=folder_id)
