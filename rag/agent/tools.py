"""
LangChain Tool Definitions
==========================
CURRENTLY EMPTY — define LangChain tools here so the LangGraph agent
in graph.py can call them dynamically.

Each tool wraps one rag submodule function. The agent decides which
tools to call based on the user message.

Expected tools:

    deg_tool
        Input:  cluster_id (str) or coords (list), work_dir (str)
        Output: same as rag.deg.get_cluster_high_expression_genes()
                {"selected_spots", "top_genes": [{"gene", "log2_fold_change", ...}]}

    pathway_tool
        Input:  genes (list[str])
        Output: same as rag.pathway.enrich_pathways()
                [{"name", "gene_count", "set_size", "pvalue", "overlap"}, ...]

    pubmed_tool
        Input:  genes (list[str]), pathways (list[str])
        Output: same as rag.pubmed.retrieve_abstracts()
                [{"pmid", "title", "journal", "year", "snippet"}, ...]

Import from the submodule __init__.py, not the implementation file directly:
    from rag.deg import get_cluster_high_expression_genes, get_roi_high_expression_genes
    from rag.pathway import enrich_pathways
    from rag.pubmed import retrieve_abstracts

After tools collect results, format the context string using:
    from rag.agent.prompt import build_prompt_context
"""
