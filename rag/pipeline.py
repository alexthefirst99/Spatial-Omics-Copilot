"""
RAG Pipeline Orchestrator
Single entry point for the full analysis pipeline:
  1. DEG extraction    (rag/deg_extraction.py)
  2. Pathway enrichment (rag/pathway_enrichment.py)
  3. PubMed retrieval  (rag/pubmed_retrieval.py)
  4. LLM context       (rag/llm_interpretation.py)

Usage:
    result = run_rag(work_dir, cluster_id="2")
    result = run_rag(work_dir, coords=[[[x,y], ...]])
    # result["gene_objects"]  — for popup card
    # result["metadata"]      — for chat UI (trace, degs, pathways, citations)
    # result["context_str"]   — for LLM prompt injection
"""

from __future__ import annotations
import math
from typing import Optional

from rag.deg import get_cluster_high_expression_genes, get_roi_high_expression_genes
from rag.pathway import enrich_pathways
from rag.pubmed import retrieve_abstracts
from rag.agent.prompt import build_prompt_context

import niceview.utils.io as vio


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
    work_dir: str,
    cluster_id: Optional[str] = None,
    coords: Optional[list] = None,
    folder_id: str = "",
    top_n: int = 25,
    n_pathways: int = 6,
    n_abstracts: int = 3,
) -> dict:
    """Run the full RAG pipeline.

    Priority: cluster_id → coords → demo fallback.

    Returns:
        gene_objects  — full DEG list for the popup card UI
        metadata      — {trace, degs, pathways, citations, label} for chat UI
        context_str   — formatted text to append to LLM prompt
    """
    # --- 1. DEG Extraction ---
    label = "selection"
    deg_result = None

    if cluster_id is not None:
        deg_result = get_cluster_high_expression_genes(
            work_dir, cluster_id, folder_id=folder_id, top_n=top_n
        )
        label = f"Cluster {cluster_id}"

    elif coords:
        deg_result = get_roi_high_expression_genes(
            work_dir, coords, folder_id=folder_id, top_n=top_n
        )
        label = "ROI"

    if deg_result and deg_result.get("top_genes"):
        gene_objects = deg_result["top_genes"]
        n_spots = deg_result.get("selected_spots", 0)
    else:
        gene_objects = _DEMO_GENE_OBJECTS
        label = label + " (demo)" if deg_result is not None else "demo"
        n_spots = 0

    genes = [g["gene"] for g in gene_objects]

    # --- 2. Pathway Enrichment ---
    pathways = enrich_pathways(genes, top_n=n_pathways)

    # --- 3. PubMed Retrieval ---
    pathway_names = [p["name"] for p in pathways]
    abstracts = retrieve_abstracts(genes, pathways=pathway_names, n=n_abstracts)

    # --- 4. LLM Context ---
    context_str = build_prompt_context(genes, pathways, abstracts, label=label)

    # --- Build UI metadata ---
    trace = [
        {
            "step": "Extracted top DEGs",
            "detail": f"{len(genes)} genes · {label}" + (f" · {n_spots} spots" if n_spots else ""),
            "icon": "deg",
        },
        {
            "step": "Pathway enrichment",
            "detail": "Reactome · GO · KEGG",
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
