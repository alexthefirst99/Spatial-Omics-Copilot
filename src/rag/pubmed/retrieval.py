"""Backward-compatible import for the renamed PubMed retrieval module.

New code should import from :mod:`rag.pubmed_retrieval`. The legacy
``rag.pubmed`` package remains available until Person 6 completes the shared
pipeline migration.
"""

from rag.pubmed_retrieval import retrieve_abstracts

__all__ = ["retrieve_abstracts"]
