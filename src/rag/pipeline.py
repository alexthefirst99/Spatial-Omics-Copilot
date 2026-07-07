"""
RAG Sequential Fallback Pipeline
=================================
Used by run_agent() (src/rag/agent/graph.py) until the real LangGraph agent
is implemented. Receives pre-computed gene_objects and runs pathway + PubMed.

Pipeline order:
  1. Pathway enrichment (GO / KEGG)
  2. PubMed retrieval

DEG is NOT run here — it runs automatically in app.py when the user selects
a cluster or ROI, and the resulting gene list is passed in as gene_objects.

Note: in the real LangGraph agent, the agent decides whether to call
pathway_tool and/or pubmed_tool. This fallback always runs both.
"""

from __future__ import annotations
import math
from typing import Optional

from rag.pathway import enrich_pathways
from rag.pubmed import retrieve_abstracts
from rag.agent.prompt import build_prompt_context


# Demo fallback when no h5ad is loaded (mixed brain cell-type profile)
_DEMO_GENE_OBJECTS = [
    {"gene": "SNAP25",  "log2_fold_change": 3.81},
    {"gene": "SYP",     "log2_fold_change": 3.44},
    {"gene": "SYT1",    "log2_fold_change": 3.12},
    {"gene": "GRIA1",   "log2_fold_change": 2.94},
    {"gene": "AIF1",    "log2_fold_change": 2.71},
    {"gene": "TREM2",   "log2_fold_change": 2.55},
    {"gene": "GFAP",    "log2_fold_change": 2.38},
    {"gene": "MBP",     "log2_fold_change": 2.20},
    {"gene": "C1QA",    "log2_fold_change": 2.05},
    {"gene": "MAPK1",   "log2_fold_change": 1.92},
    {"gene": "SPP1",    "log2_fold_change": 1.80},
    {"gene": "OLIG2",   "log2_fold_change": 1.68},
]


def _run_sequential(
    gene_objects: list,
    message: str = "",
    label: str = "selection",
    n_pathways: int = 6,
    n_abstracts: int = 3,
) -> dict:
    # Use demo fallback if no genes provided
    if not gene_objects:
        gene_objects = _DEMO_GENE_OBJECTS
        label = "demo"

    genes = [g["gene"] for g in gene_objects]

    # --- 1. Pathway Enrichment ---
    pathways = enrich_pathways(genes, top_n=n_pathways)

    # --- 2. PubMed Retrieval ---
    pathway_names = [p["name"] for p in pathways]
    abstracts = retrieve_abstracts(genes, pathways=pathway_names, n=n_abstracts)

    # --- 3. LLM Context ---
    context_str = build_prompt_context(genes, pathways, abstracts, label=label)

    # --- Build UI metadata ---
    trace = [
        {
            "step": "Extracted top DEGs",
            "detail": f"{len(genes)} genes · {label}",
            "icon": "deg",
        },
        {
            "step": "Pathway enrichment",
            "detail": "GO · KEGG",
            "icon": "pathway",
        },
        {
            "step": f"Retrieved {len(abstracts)} PubMed abstract{'s' if len(abstracts) != 1 else ''}",
            "detail": "",
            "icon": "pubmed",
        },
    ]

    pathway_bars = []
    for p in pathways:
        neg_log10p = round(-math.log10(max(p["pvalue"], 1e-15)), 1)
        source, short_name = (p["name"].split(" · ", 1) if " · " in p["name"] else ("", p["name"]))
        pathway_bars.append({
            "source": source,
            "name": short_name,
            "gene_count": p["gene_count"],
            "neg_log10p": neg_log10p,
        })

    degs = [
        {"gene": g["gene"], "log2fc": round(g["log2_fold_change"], 2)}
        for g in gene_objects[:8]
    ]

    citations = [
        {"id": i + 1, "pmid": ab["pmid"], "title": ab["title"],
         "journal": ab["journal"], "year": ab["year"]}
        for i, ab in enumerate(abstracts)
    ]

    return {
        "gene_objects": gene_objects,
        "context_str": context_str,
        "metadata": {
            "trace": trace,
            "degs": degs,
            "pathways": pathway_bars,
            "citations": citations,
            "label": label,
        },
    }
