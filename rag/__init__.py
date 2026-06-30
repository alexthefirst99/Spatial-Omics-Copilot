# RAG pipeline for spatial omics copilot.
# DEG extraction (both ROI and cluster) is already done in niceview/interface/roi.py:
#   get_roi_high_expression_genes()     — genes enriched in a drawn ROI polygon
#   get_cluster_high_expression_genes() — genes enriched in a spatial cluster
# Each module here is a downstream pipeline stage that consumes that gene list:
#   pathway_enrichment.py — ORA/GSEA against GO, Reactome, KEGG
#   pubmed_retrieval.py   — fetch relevant abstracts via NCBI E-utilities
#   llm_interpretation.py — format retrieved evidence for LLM context injection
#   pipeline.py           — orchestrates all stages; returns metadata for the UI
