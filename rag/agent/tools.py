"""
LangChain Tool Definitions
==========================
CURRENTLY EMPTY — define LangChain tools here for the LangGraph agent.

DEG extraction is NOT a tool — it runs automatically before the agent
starts, triggered by the user's cluster/ROI selection in the UI.
The agent only decides whether to call pathway_tool and/or pubmed_tool.

Tools to implement:

    pathway_tool
        When:   agent decides pathway context is relevant to the question
        Input:  genes (list[str]) — from the DEG result already computed
        Output: same as rag.pathway.enrich_pathways()
                [{"name", "gene_count", "set_size", "pvalue", "overlap"}, ...]

    pubmed_tool
        When:   agent decides literature context is relevant to the question
        Input:  genes (list[str]), message (str) — message used to refine query
        Output: same as rag.pubmed.retrieve_abstracts()
                [{"pmid", "title", "journal", "year", "snippet"}, ...]

Import from the submodule __init__.py, not the implementation file directly:
    from rag.pathway import enrich_pathways
    from rag.pubmed import retrieve_abstracts

After tools collect results, format the context string using:
    from rag.agent.prompt import build_prompt_context
"""
