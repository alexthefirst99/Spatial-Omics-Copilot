"""Backward-compatible pathway API backed by real Enrichr enrichment.

New code should import :func:`rag.pathway_enrichment.run_pathway_enrichment`.
This adapter preserves the legacy list-of-dicts format used by the current UI.
"""

from __future__ import annotations

from rag.pathway_enrichment import run_pathway_enrichment


def enrich_pathways(genes: list[str], top_n: int = 6) -> list[dict]:
    """Return real Enrichr results in the legacy UI dictionary shape."""

    result = run_pathway_enrichment(
        genes,
        config={
            "pathway_enrichment": {
                "top_n": top_n,
                "significant_only": True,
            }
        },
    )
    pathways: list[dict] = []
    for item in result.pathways:
        display_name = f"{item.source} · {item.name}" if item.source else item.name
        pathways.append(
            {
                "name": display_name,
                "gene_count": item.overlap_count,
                "set_size": item.gene_set_size,
                "pvalue": item.adjusted_p_value,
                "overlap": list(item.overlap_genes),
                "source": item.source,
            }
        )
    return pathways


__all__ = ["enrich_pathways"]
