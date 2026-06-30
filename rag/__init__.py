# RAG analysis layer for spatial omics copilot.
#
# DEG extraction runs automatically in app.py when the user selects a cluster
# or ROI. The resulting gene list is cached and passed to run_agent().
#
# Subpackages:
#   rag/deg/       — DEG extraction (cluster vs non-cluster, ROI vs non-ROI)
#   rag/pathway/   — pathway enrichment ORA against GO / KEGG
#   rag/pubmed/    — PubMed abstract retrieval via NCBI E-utilities
#   rag/agent/     — run_agent() entry point; assembles context_str for the LLM
#   rag/pipeline.py — fallback sequential pipeline (pathway + PubMed) until LangGraph is ready
#
# Only import from subpackage __init__.py files, not implementation modules directly.
