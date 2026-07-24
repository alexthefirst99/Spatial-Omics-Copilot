"""Public PubMed retrieval workflows."""

from __future__ import annotations

from collections.abc import Iterable
import re
from typing import Any

from .client import NCBIEUtilitiesClient, PubMedClientError
from .models import PubMedResult
from .query import build_pubmed_query


_SENTENCE_END_RE = re.compile(r"(?<=[.!?])\s+")


def _safe_limit(value: object, *, maximum: int = 100) -> int:
    try:
        return min(max(int(value), 0), maximum)
    except (TypeError, ValueError, OverflowError):
        return 0


def search_pubmed(
    query: str,
    max_results: int = 5,
    *,
    client: Any = None,
    timeout: float = 10.0,
    max_retries: int = 2,
) -> PubMedResult:
    """Run NCBI ESearch followed by batched EFetch.

    All expected input, network, rate-limit, timeout, and XML failures are
    returned as an empty ``PubMedResult`` with a status message. This function
    is therefore safe to call from a background RAG tool without wrapping it in
    an additional exception handler.
    """

    query_text = " ".join(str(query or "").split())
    limit = _safe_limit(max_results)
    if not query_text:
        return PubMedResult(
            papers=[],
            query="",
            status_message="No PubMed query was provided.",
        )
    if limit == 0:
        return PubMedResult(
            papers=[],
            query=query_text,
            status_message="PubMed max_results must be greater than zero.",
        )

    active_client: Any = None
    owns_client = client is None
    try:
        active_client = (
            client
            if client is not None
            else NCBIEUtilitiesClient(
                timeout=timeout,
                max_retries=max_retries,
            )
        )
        pmids = active_client.search_ids(query_text, max_results=limit)
        if not pmids:
            return PubMedResult(
                papers=[],
                query=query_text,
                status_message="No matching PubMed papers were found.",
            )

        papers = active_client.fetch_articles(pmids)[:limit]
        if not papers:
            return PubMedResult(
                papers=[],
                query=query_text,
                status_message=(
                    "PubMed returned identifiers, but no citation records "
                    "could be parsed."
                ),
            )

        return PubMedResult(
            papers=papers,
            query=query_text,
            status_message=(
                f"Retrieved {len(papers)} PubMed paper"
                f"{'' if len(papers) == 1 else 's'}."
            ),
        )
    except (PubMedClientError, ValueError, TypeError, TimeoutError, OSError) as exc:
        return PubMedResult(
            papers=[],
            query=query_text,
            status_message=(
                "PubMed retrieval is unavailable "
                f"({exc.__class__.__name__})."
            ),
        )
    except Exception as exc:
        # Third-party Session/test-double implementations can raise their own
        # exception types. Keep the RAG boundary fail-safe without exposing
        # request URLs, API keys, or response bodies in the status message.
        return PubMedResult(
            papers=[],
            query=query_text,
            status_message=(
                "PubMed retrieval is unavailable "
                f"({exc.__class__.__name__})."
            ),
        )
    finally:
        if owns_client and active_client is not None:
            closer = getattr(active_client, "close", None)
            if callable(closer):
                try:
                    closer()
                except Exception:
                    pass


def _as_list(values: Iterable[object] | None) -> list[object]:
    if values is None:
        return []
    if isinstance(values, (str, bytes)):
        return [values]
    try:
        return list(values)
    except TypeError:
        return []


def _has_meaningful_term(values: Iterable[object]) -> bool:
    return any(value is not None and str(value).strip() for value in values)


def _snippet(abstract: str, *, max_sentences: int = 3, max_chars: int = 700) -> str:
    text = " ".join(str(abstract or "").split())
    if not text:
        return ""
    sentences = _SENTENCE_END_RE.split(text)
    snippet = " ".join(sentences[:max_sentences]).strip()
    if len(snippet) <= max_chars:
        return snippet
    clipped = snippet[: max_chars + 1].rsplit(" ", 1)[0].rstrip(" ,;:")
    return clipped + "…"


def retrieve_abstracts(
    genes: Iterable[object] | None,
    pathways: Iterable[object] | None = None,
    n: int = 3,
    *,
    disease: str = "colorectal cancer",
    client: Any = None,
) -> list[dict]:
    """Compatibility adapter for the existing RAG pipeline.

    Unlike the former mock, this function never pads results with unrelated
    papers. It maps real ``PubMedResult`` records to the list-of-dicts schema
    currently consumed by ``rag.pipeline`` and the chat citation panel.
    """

    gene_terms = _as_list(genes)
    pathway_terms = _as_list(pathways)
    limit = _safe_limit(n)
    if (
        limit == 0
        or not (
            _has_meaningful_term(gene_terms)
            or _has_meaningful_term(pathway_terms)
        )
    ):
        return []

    query = build_pubmed_query(
        genes=gene_terms,
        pathways=pathway_terms,
        disease=disease,
    )
    result = search_pubmed(
        query=query,
        max_results=limit,
        client=client,
    )
    return [
        {
            "pmid": paper.pmid,
            "title": paper.title,
            "journal": paper.journal,
            "year": paper.year,
            "snippet": _snippet(paper.abstract),
        }
        for paper in result.papers
    ]


__all__ = ["retrieve_abstracts", "search_pubmed"]
