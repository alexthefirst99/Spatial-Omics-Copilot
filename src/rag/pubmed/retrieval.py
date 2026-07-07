"""
PubMed Retrieval Module
=======================
CURRENTLY MOCK — returns abstracts from a hardcoded list of 5 curated papers.
Replace retrieve_abstracts() with real NCBI E-utilities API calls.

API docs: https://www.ncbi.nlm.nih.gov/home/develop/api/
API key:  set PUBMED_API_KEY in .env for higher rate limits (10 req/s vs 3)

Input:
    genes    : list[str]       — gene symbols to search for
    pathways : list[str]       — pathway names to include in query (optional)
    n        : int             — number of abstracts to return (default 3)

Expected output — list of dicts:
    [
        {
            "pmid":    "38912204",
                       # PubMed ID — used to build citation links in the UI
            "title":   "Spatial transcriptomics reveals...",
                       # full paper title
            "journal": "Nature Cancer",
                       # journal name
            "year":    2024,
                       # publication year
            "snippet": "Spatial analysis identified a population of...",
                       # 2-3 sentence summary of the abstract
                       # used as context for the LLM — keep it factual
        },
        ...
    ]
    Always return exactly n results. If fewer are found, pad with less
    relevant results rather than returning an empty list.

DO NOT CHANGE the output format — pipeline.py and the UI depend on it.
"""

from __future__ import annotations

# ---------------------------------------------------------------------------
# Curated abstract database (real version pulls from NCBI live)
# ---------------------------------------------------------------------------
_ABSTRACTS: list[dict] = [
    {
        "pmid": "38912204",
        "title": "Spatial transcriptomics reveals tumor microenvironment heterogeneity at the invasive margin",
        "journal": "Nature Cancer",
        "year": 2024,
        "genes": {"EPCAM", "KRT8", "CD8A", "SPP1", "COL1A1", "VIM", "CDH2", "FN1", "TWIST1", "SNAI1"},
        "pathways": {"epithelial-mesenchymal", "emt", "cell adhesion", "adaptive immune"},
        "snippet": (
            "Spatial analysis identified a population of EPCAM+/VIM+ hybrid cells "
            "at the tumor invasive front, co-localized with CD8A+ cytotoxic T cells. "
            "Pathway enrichment in this zone revealed activation of EMT regulators "
            "and antigen processing machinery, consistent with an immune-engaged "
            "invasive phenotype."
        ),
    },
    {
        "pmid": "37891452",
        "title": "Single-cell and spatial analysis of neuroinflammatory signatures in Alzheimer's disease",
        "journal": "Cell",
        "year": 2023,
        "genes": {"AIF1", "TREM2", "C1QA", "GFAP", "AQP4", "P2RY12", "SPP1", "APOE", "TYROBP", "HEXB"},
        "pathways": {"innate immune", "inflammatory", "microglia", "astrocyte"},
        "snippet": (
            "Activated microglia (TREM2+SPP1+) were spatially co-localized with "
            "amyloid plaques, with elevated C1Q complement and lysosomal gene "
            "expression. Reactive astrocytes (GFAP+AQP4−) formed a secondary "
            "neuroprotective layer limited to <100 μm from plaque cores."
        ),
    },
    {
        "pmid": "39012345",
        "title": "Spatial atlas of cortical synaptic gene expression programs across laminar boundaries",
        "journal": "Science",
        "year": 2024,
        "genes": {"SNAP25", "SYP", "GRIA1", "GRIN2B", "DLG4", "SHANK3", "NRXN1", "SYT1", "CACNA1A", "STXBP1"},
        "pathways": {"synaptic transmission", "neurotransmitter release", "serotonergic"},
        "snippet": (
            "Layer-specific synaptic gene modules were resolved at single-spot "
            "resolution. Layer 4 was defined by high GRIA1/GRIN2A expression and "
            "dense thalamocortical innervation, while layer 5 pyramidal neurons "
            "expressed elevated CACNA1A and SYT1, consistent with corticospinal "
            "output specialization."
        ),
    },
    {
        "pmid": "38445621",
        "title": "MAPK and PI3K co-activation defines an immune-excluded spatial subtype in glioblastoma",
        "journal": "Nature Medicine",
        "year": 2024,
        "genes": {"EGFR", "MAPK1", "PIK3CA", "PTEN", "AKT1", "PDGFRA", "MET", "RAF1", "MAP2K1", "BRAF"},
        "pathways": {"mapk", "pi3k", "akt", "signaling"},
        "snippet": (
            "Spatial co-occurrence of EGFR amplification and PTEN loss defined a "
            "12% subpopulation of tumor spots with concurrent MAPK/PI3K activation. "
            "These regions showed significantly reduced immune infiltration and "
            "correlated with poor survival, suggesting immune exclusion as a "
            "MAPK-PI3K co-activation phenotype."
        ),
    },
    {
        "pmid": "38671033",
        "title": "Myelination heterogeneity in the human cortex revealed by spatial transcriptomics",
        "journal": "Nature Neuroscience",
        "year": 2024,
        "genes": {"MBP", "MOG", "PLP1", "MAG", "OLIG1", "OLIG2", "SOX10", "MYRF", "CLDN11", "UGT8"},
        "pathways": {"myelination", "oligodendrocyte", "glial"},
        "snippet": (
            "Deep-layer cortical regions showed increased MBP and MOG expression "
            "compared to superficial layers, with OLIG2+ oligodendrocyte precursors "
            "concentrated at the grey-white matter boundary. SOX10 expression "
            "correlated with local myelination index across all 10 cortical regions."
        ),
    },
]


def retrieve_abstracts(
    genes: list[str],
    pathways: list[str] | None = None,
    n: int = 3,
) -> list[dict]:
    """Return the most relevant PubMed abstracts for the given gene list.

    Each returned dict has:
        pmid, title, journal, year, snippet
    """
    # TODO (teammate): replace body with live NCBI E-utilities calls:
    #   base = "https://eutils.ncbi.nlm.nih.gov/entrez/eutils/"
    #   query = " OR ".join(f'"{g}"[Gene Name]' for g in genes[:10])
    #   esearch → get PMIDs → efetch → parse abstracts

    gene_set = set(g.upper() for g in genes)
    pathway_text = " ".join(pathways or []).lower()

    scored: list[tuple[float, dict]] = []
    for abstract in _ABSTRACTS:
        gene_overlap = len(gene_set & abstract["genes"])
        pathway_match = sum(
            1 for kw in abstract["pathways"]
            if kw in pathway_text
        )
        score = gene_overlap * 2 + pathway_match
        if score > 0:
            scored.append((score, abstract))

    scored.sort(key=lambda x: -x[0])

    # Fallback: return top abstracts by gene overlap even if pathway text is empty
    if not scored:
        for abstract in _ABSTRACTS:
            gene_overlap = len(gene_set & abstract["genes"])
            if gene_overlap > 0:
                scored.append((gene_overlap, abstract))
        scored.sort(key=lambda x: -x[0])

    # Pad with remaining abstracts (score 0) so we always return n results
    seen_pmids = {a["pmid"] for _, a in scored}
    for abstract in _ABSTRACTS:
        if len(scored) >= n:
            break
        if abstract["pmid"] not in seen_pmids:
            scored.append((0, abstract))
            seen_pmids.add(abstract["pmid"])

    return [
        {
            "pmid": a["pmid"],
            "title": a["title"],
            "journal": a["journal"],
            "year": a["year"],
            "snippet": a["snippet"],
        }
        for _, a in scored[:n]
    ]
