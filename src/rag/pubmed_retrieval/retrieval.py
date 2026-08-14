"""Public PubMed retrieval workflows."""

from __future__ import annotations

from collections.abc import Iterable
import re
from typing import Any

from .client import NCBIEUtilitiesClient, PubMedClientError
from .models import PubMedPaper, PubMedResult
from .query import build_pubmed_query


_SENTENCE_END_RE = re.compile(r"(?<=[.!?])\s+")
_TOKEN_RE = re.compile(r"[A-Za-z][A-Za-z0-9-]{2,}")

# Generic words add almost no ROI specificity when comparing a research
# question/pathway label with a PubMed abstract. Keeping them out of the
# lightweight re-ranker lets gene symbols and biologically descriptive terms
# dominate without requiring an embedding model or an extra network service.
_RERANK_STOPWORDS = frozenset(
    {
        "about", "against", "analysis", "and", "biological", "cancer",
        "cell", "cells", "colorectal", "differential", "disease", "from",
        "gene", "genes", "human", "identify", "inference", "literature",
        "morphology", "pathway", "pathways", "region", "relevant", "roi",
        "signaling", "specific", "state", "supporting", "tissue", "visible",
        "with",
    }
)


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


def _normalise_terms(values: Iterable[object] | None, *, limit: int) -> list[str]:
    """Return clean, de-duplicated ranking terms while preserving order."""

    if values is None:
        return []
    if isinstance(values, (str, bytes)):
        values = [values]
    output: list[str] = []
    seen: set[str] = set()
    try:
        iterator = iter(values)
    except TypeError:
        return []
    for value in iterator:
        text = " ".join(str(value or "").split()).strip()
        key = text.casefold()
        if text and key not in seen:
            seen.add(key)
            output.append(text)
            if len(output) >= limit:
                break
    return output


def _contains_symbol(text: str, symbol: str) -> bool:
    """Match a gene-like symbol as a complete alphanumeric token."""

    if not text or not symbol:
        return False
    return bool(
        re.search(
            rf"(?<![A-Za-z0-9]){re.escape(symbol)}(?![A-Za-z0-9])",
            text,
            flags=re.IGNORECASE,
        )
    )


def _content_tokens(text: str) -> set[str]:
    return {
        token.casefold()
        for token in _TOKEN_RE.findall(text or "")
        if token.casefold() not in _RERANK_STOPWORDS
    }


def _paper_relevance_score(
    paper: PubMedPaper,
    *,
    genes: list[str],
    pathways: list[str],
    disease: str,
    question: str,
) -> tuple[float, int]:
    """Score one candidate using only observable ROI/query evidence.

    The NCBI ESearch call already sorts by relevance. This second pass is not a
    replacement search engine; it only promotes candidates that explicitly
    mention the strongest ROI genes/pathway concepts. The second tuple value is
    the number of ROI-evidence matches and is used as an additional tie-breaker.
    """

    title = paper.title or ""
    abstract = paper.abstract or ""
    title_fold = title.casefold()
    abstract_fold = abstract.casefold()

    score = 0.0
    evidence_hits = 0

    for gene in genes:
        if _contains_symbol(title, gene):
            score += 8.0
            evidence_hits += 1
        elif _contains_symbol(abstract, gene):
            score += 4.0
            evidence_hits += 1

    for pathway in pathways:
        phrase = pathway.casefold().strip()
        if not phrase:
            continue
        if phrase in title_fold:
            score += 6.0
            evidence_hits += 1
            continue
        if phrase in abstract_fold:
            score += 3.0
            evidence_hits += 1
            continue

        # Enrichr pathway labels are often longer than the wording used in an
        # abstract. Reward partial overlap of informative pathway words rather
        # than requiring the exact ontology label.
        pathway_tokens = _content_tokens(pathway)
        if pathway_tokens:
            title_overlap = len(pathway_tokens & _content_tokens(title))
            abstract_overlap = len(pathway_tokens & _content_tokens(abstract))
            overlap = max(title_overlap, abstract_overlap)
            if overlap >= min(2, len(pathway_tokens)):
                score += min(4.0, overlap * 1.25)
                evidence_hits += 1

    disease_text = " ".join(str(disease or "").split()).casefold()
    if disease_text:
        if disease_text in title_fold:
            score += 2.0
        elif disease_text in abstract_fold:
            score += 1.0

    question_tokens = _content_tokens(question)
    if question_tokens:
        score += 0.6 * len(question_tokens & _content_tokens(title))
        score += 0.2 * len(question_tokens & _content_tokens(abstract))

    # A disease-only paper can rank highly in PubMed while saying nothing about
    # this particular ROI. Keep it as a fallback candidate but push it below
    # papers tied to measured ROI evidence.
    if evidence_hits == 0:
        score -= 2.0

    return score, evidence_hits


def rerank_pubmed_result(
    pubmed_result: PubMedResult,
    *,
    genes: Iterable[object] | None = None,
    pathways: Iterable[object] | None = None,
    disease: str = "",
    question: str = "",
    top_k: int = 3,
) -> PubMedResult:
    """Deterministically keep the PubMed candidates most specific to the ROI.

    This is deliberately local and dependency-free. It makes candidate-pool
    retrieval useful even when Chroma/embedding downloads are unavailable, and
    it never fabricates papers: every returned record is one fetched from
    PubMed in the current call.
    """

    limit = _safe_limit(top_k)
    papers = list(getattr(pubmed_result, "papers", []) or [])
    if limit <= 0 or not papers:
        return PubMedResult(
            papers=[],
            query=getattr(pubmed_result, "query", ""),
            status_message=getattr(pubmed_result, "status_message", ""),
        )

    gene_terms = [term.upper() for term in _normalise_terms(genes, limit=12)]
    pathway_terms = _normalise_terms(pathways, limit=6)

    scored: list[tuple[float, int, int, PubMedPaper]] = []
    for index, paper in enumerate(papers):
        score, evidence_hits = _paper_relevance_score(
            paper,
            genes=gene_terms,
            pathways=pathway_terms,
            disease=disease,
            question=question,
        )
        scored.append((score, evidence_hits, index, paper))

    scored.sort(key=lambda item: (-item[0], -item[1], item[2]))
    selected = [item[3] for item in scored[:limit]]
    return PubMedResult(
        papers=selected,
        query=getattr(pubmed_result, "query", ""),
        status_message=(
            f"Retrieved {len(papers)} PubMed candidate(s); retained "
            f"{len(selected)} ROI-focused paper(s)."
        ),
    )


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


__all__ = ["rerank_pubmed_result", "retrieve_abstracts", "search_pubmed"]
