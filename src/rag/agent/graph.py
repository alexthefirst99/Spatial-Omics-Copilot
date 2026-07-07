"""
LangGraph Agent
===============
CURRENTLY MOCK — run_agent() just calls the fixed sequential pipeline.
Replace with a real LangGraph agent.

Future workflow:
    1. DEG runs automatically when user selects cluster/ROI in the UI (app.py).
       Gene list is cached before run_agent() is ever called.

    2. run_agent() receives the cached gene list.
       Agent feeds genes to its tools:
           pathway_tool(genes)         — GO / KEGG enrichment
           pubmed_tool(genes, message) — PubMed retrieval

    3. Agent assembles DEG + pathway results + PubMed abstracts
       into one context_str via prompt.py.

    4. context_str goes to worker.py → LLM.
       DEG never goes directly to the LLM — it always goes through
       the agent first so it can be enriched with pathway and literature context.

Input:
    gene_objects : list[dict]  — DEG result already computed by app.py on selection
                                 [{"gene": "SNAP25", "log2_fold_change": 3.81}, ...]
                                 pass [] to use demo fallback genes
    message      : str         — user chat message; agent uses this to decide
                                 which tools to call and how to query PubMed
    label        : str         — region label for UI headers e.g. "Cluster 5", "ROI"

Expected output — dict:
    {
        "gene_objects": [
            {"gene": "SNAP25", "log2_fold_change": 3.81},
            ...
        ],
        # Full DEG list — used by app.py for the cluster gene popup card.

        "context_str": "\n\nRAG-retrieved biological context...",
        # Formatted evidence string injected into the LLM prompt by worker.py.

        "metadata": {
            "trace": [
                {"step": "Pathway enrichment", "detail": "GO · KEGG", "icon": "pathway"},
                {"step": "Retrieved 3 PubMed abstracts", "detail": "", "icon": "pubmed"},
                ...
            ],
            # Only steps the agent actually ran — shown in AGENT TRACE card.
            # DEG is always included since it always runs.
            # icon values: "deg", "pathway", "pubmed"

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
    gene_objects: list,
    message: str = "",
    label: str = "selection",
) -> dict:
    """
    MOCK: falls back to fixed sequential pipeline until LangGraph is implemented.
    Replace this function body with the real LangGraph agent (see instructions above).
    """
    from rag.pipeline import _run_sequential
    return _run_sequential(gene_objects, message=message, label=label)
