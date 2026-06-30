"""
LLM Interpretation Module
=========================
CURRENTLY BASIC — formats DEGs, pathways, and abstracts into a plain text
block. Works but has no domain framing, no chain-of-thought instruction,
and no structure guiding the LLM on how to reason about spatial omics.
Improve build_prompt_context() to produce a better-structured context.

Input:
    genes     : list[str]   — gene symbols, e.g. ["SNAP25", "TREM2"]
    pathways  : list[dict]  — enriched pathway dicts from pathway module
    abstracts : list[dict]  — abstract dicts from pubmed module
    label     : str         — selection name, e.g. "Cluster 2" or "ROI"

Expected output — a single string:
    - Must start with "

" so it safely appends to the user prompt
    - Tells the LLM what genes, pathways, and papers are relevant
    - Instructs the LLM to cite papers inline as [1], [2], etc.
    - Should guide the LLM to identify cell type, pathway activity,
      and clinical relevance — not just list the data

    Example structure:
        

You are analyzing spatial transcriptomics data for [label].
        Top expressed genes: SNAP25, SYP, GRIA1 ...
        Enriched pathways: Chemical synaptic transmission (p=1.2e-5) ...
        Relevant publications:
          [1] "Spatial atlas of cortical..." (Science 2024, PMID:39012345)
        Respond in 3-4 sentences. Cite papers as [1], [2] where relevant.

DO NOT CHANGE the function signature or return type — worker.py appends
this string directly to the user prompt.
"""

from __future__ import annotations


def build_prompt_context(
    genes: list[str],
    pathways: list[dict],
    abstracts: list[dict],
    label: str = "selection",
) -> str:
    """Format RAG evidence into a context string for LLM prompt injection.

    Args:
        genes:     gene symbols from DEG extraction
        pathways:  enriched pathway dicts from pathway_enrichment.enrich_pathways()
        abstracts: abstract dicts from pubmed_retrieval.retrieve_abstracts()
        label:     human-readable selection name (e.g. "Cluster 2", "ROI")

    Returns a plain-text block starting with \\n\\n so it safely appends
    to an existing prompt string.
    """
    lines: list[str] = [
        f"\n\nRAG-retrieved biological context for {label}:",
    ]

    if pathways:
        lines.append("Top enriched pathways (p-value, gene overlap):")
        for p in pathways:
            overlap_str = ", ".join(p["overlap"][:5])
            if len(p["overlap"]) > 5:
                overlap_str += f" +{len(p['overlap']) - 5} more"
            lines.append(
                f"  - {p['name']}: p={p['pvalue']:.2e}, "
                f"{p['gene_count']}/{p['set_size']} genes [{overlap_str}]"
            )

    if abstracts:
        lines.append(
            "\nRelevant publications (cite inline as [1], [2], ... in your response):"
        )
        for i, ab in enumerate(abstracts, 1):
            lines.append(
                f"  [{i}] \"{ab['title']}\" "
                f"({ab['journal']}, {ab['year']}, PMID:{ab['pmid']})"
            )
            lines.append(f"      {ab['snippet'][:300]}")

    lines.append(
        "\nWhen writing your response, reference [1], [2], etc. "
        "where the findings are relevant. Be concise."
    )

    return "\n".join(lines)
