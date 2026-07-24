"""PubMed literature retrieval public API."""

from .client import (
    NCBIEUtilitiesClient,
    PubMedClientError,
    PubMedParseError,
    PubMedRequestError,
    RateLimiter,
    parse_esearch_xml,
    parse_pubmed_xml,
)
from .models import PubMedPaper, PubMedResult
from .query import build_pubmed_query
from .retrieval import retrieve_abstracts, search_pubmed
from .vector_store import semantic_search_abstracts

__all__ = [
    "NCBIEUtilitiesClient",
    "PubMedClientError",
    "PubMedPaper",
    "PubMedParseError",
    "PubMedRequestError",
    "PubMedResult",
    "RateLimiter",
    "build_pubmed_query",
    "parse_esearch_xml",
    "parse_pubmed_xml",
    "retrieve_abstracts",
    "search_pubmed",
    "semantic_search_abstracts",
]
