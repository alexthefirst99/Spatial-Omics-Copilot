"""NCBI E-utilities client and PubMed XML parsers.

The client deliberately keeps HTTP concerns separate from result construction:
``esearch`` returns PMIDs, then one batched ``efetch`` request returns the
corresponding citation records. Network calls are rate-limited and retried with
bounded backoff; parsing helpers remain deterministic and easy to unit test.
"""

from __future__ import annotations

from collections.abc import Callable, Iterable, Mapping
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
import json
import os
import re
import threading
import time
from typing import Any
import xml.etree.ElementTree as ET

import requests

from .models import PubMedPaper


EUTILS_BASE_URL = "https://eutils.ncbi.nlm.nih.gov/entrez/eutils/"
_RETRYABLE_STATUS_CODES = {408, 425, 429, 500, 502, 503, 504}
_YEAR_RE = re.compile(r"\b((?:18|19|20|21)\d{2})\b")


class PubMedClientError(RuntimeError):
    """Base error for handled NCBI request or response failures."""


class PubMedRequestError(PubMedClientError):
    """Raised after an HTTP request cannot be completed safely."""


class PubMedParseError(PubMedClientError):
    """Raised when NCBI returns malformed or explicitly erroneous XML."""


class RateLimiter:
    """Thread-safe start-rate limiter using a monotonic clock."""

    def __init__(
        self,
        requests_per_second: float,
        *,
        sleeper: Callable[[float], None] = time.sleep,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        rate = float(requests_per_second)
        if rate <= 0:
            raise ValueError("requests_per_second must be positive")
        self._interval = 1.0 / rate
        self._sleeper = sleeper
        self._clock = clock
        self._lock = threading.Lock()
        self._last_started: float | None = None

    def wait(self) -> None:
        """Wait until another request may start."""

        with self._lock:
            now = self._clock()
            if self._last_started is None:
                self._last_started = now
                return

            earliest = self._last_started + self._interval
            delay = earliest - now
            if delay > 0:
                self._sleeper(delay)
            # A fake sleeper used by tests may not advance its clock. Keeping
            # the scheduled time still prevents the next call from bursting.
            self._last_started = max(self._clock(), earliest)


_SHARED_LIMITERS: dict[bool, RateLimiter] = {}
_SHARED_LIMITERS_LOCK = threading.Lock()


def _shared_limiter(has_api_key: bool) -> RateLimiter:
    with _SHARED_LIMITERS_LOCK:
        limiter = _SHARED_LIMITERS.get(has_api_key)
        if limiter is None:
            limiter = RateLimiter(10.0 if has_api_key else 3.0)
            _SHARED_LIMITERS[has_api_key] = limiter
        return limiter


def _normalise_space(value: object) -> str:
    if value is None:
        return ""
    return " ".join(str(value).split())


def _node_text(node: ET.Element | None) -> str:
    if node is None:
        return ""
    return _normalise_space("".join(node.itertext()))


def _xml_root(payload: bytes | str) -> ET.Element:
    if not payload:
        raise PubMedParseError("NCBI returned an empty XML response.")
    try:
        return ET.fromstring(payload)
    except (ET.ParseError, TypeError, ValueError) as exc:
        raise PubMedParseError("NCBI returned malformed XML.") from exc


def parse_esearch_xml(payload: bytes | str) -> list[str]:
    """Parse a PubMed ESearch XML response into unique PMIDs."""

    root = _xml_root(payload)
    explicit_error = root.find(".//ERROR")
    if explicit_error is not None:
        detail = _node_text(explicit_error) or "unknown ESearch error"
        raise PubMedParseError(f"NCBI ESearch error: {detail}")
    error_list = root.find(".//ErrorList")
    if error_list is not None and list(error_list):
        details = [
            f"{child.tag}: {_node_text(child)}"
            for child in error_list
            if _node_text(child)
        ]
        raise PubMedParseError(
            "NCBI rejected part of the PubMed query"
            + (f" ({'; '.join(details)})." if details else ".")
        )

    pmids: list[str] = []
    seen: set[str] = set()
    for node in root.findall(".//IdList/Id"):
        pmid = _node_text(node)
        if pmid and not pmid.isdigit():
            raise PubMedParseError("NCBI ESearch returned an invalid PMID.")
        if pmid and pmid not in seen:
            seen.add(pmid)
            pmids.append(pmid)
    return pmids


def _publication_year(article: ET.Element) -> int | None:
    paths = (
        "./MedlineCitation/Article/Journal/JournalIssue/PubDate/Year",
        "./MedlineCitation/Article/ArticleDate/Year",
    )
    for path in paths:
        text = _normalise_space(article.findtext(path))
        if text.isdigit() and len(text) == 4:
            return int(text)

    medline_date = _normalise_space(
        article.findtext(
            "./MedlineCitation/Article/Journal/JournalIssue/PubDate/MedlineDate"
        )
    )
    match = _YEAR_RE.search(medline_date)
    return int(match.group(1)) if match else None


def _abstract_container_text(container: ET.Element | None) -> str:
    if container is None:
        return ""

    sections: list[str] = []
    for node in container.findall("./AbstractText"):
        text = _node_text(node)
        if not text:
            continue
        label = _normalise_space(node.attrib.get("Label"))
        if label and not text.casefold().startswith(label.casefold() + ":"):
            text = f"{label}: {text}"
        sections.append(text)
    return " ".join(sections)


def _article_abstract_text(
    record: ET.Element,
    article_node: ET.Element | None,
) -> str:
    primary = _abstract_container_text(
        article_node.find("./Abstract") if article_node is not None else None
    )
    if primary:
        return primary

    alternatives = record.findall("./MedlineCitation/OtherAbstract")
    if not alternatives:
        return ""
    alternatives.sort(
        key=lambda node: (
            node.attrib.get("Language", "eng").casefold() != "eng",
            node.attrib.get("Type", "").casefold() != "plain-language-summary",
        )
    )
    return _abstract_container_text(alternatives[0])


def _book_publication_year(record: ET.Element) -> int | None:
    for path in (
        "./BookDocument/Book/PubDate/Year",
        "./BookDocument/ContributionDate/Year",
    ):
        text = _normalise_space(record.findtext(path))
        if text.isdigit() and len(text) == 4:
            return int(text)
    medline_date = _normalise_space(
        record.findtext("./BookDocument/Book/PubDate/MedlineDate")
    )
    match = _YEAR_RE.search(medline_date)
    return int(match.group(1)) if match else None


def _parse_book_articles(root: ET.Element) -> list[PubMedPaper]:
    papers: list[PubMedPaper] = []
    for record in root.findall(".//PubmedBookArticle"):
        document = record.find("./BookDocument")
        if document is None:
            continue
        pmid = _normalise_space(document.findtext("./PMID"))
        if not pmid.isdigit():
            continue
        book_title = _node_text(document.find("./Book/BookTitle"))
        article_title = _node_text(document.find("./ArticleTitle"))
        title = article_title or book_title
        publisher = _normalise_space(
            document.findtext("./Book/Publisher/PublisherName")
        )
        papers.append(
            PubMedPaper(
                pmid=pmid,
                title=title,
                abstract=_abstract_container_text(document.find("./Abstract")),
                journal=book_title if article_title else publisher,
                year=_book_publication_year(record),
            )
        )
    return papers


def parse_pubmed_xml(payload: bytes | str) -> list[PubMedPaper]:
    """Parse PubMed EFetch XML into citation records with provenance."""

    root = _xml_root(payload)
    explicit_error = root.find(".//ERROR")
    if explicit_error is not None:
        detail = _node_text(explicit_error) or "unknown EFetch error"
        raise PubMedParseError(f"NCBI EFetch error: {detail}")

    papers: list[PubMedPaper] = []
    seen_pmids: set[str] = set()
    for record in root.findall(".//PubmedArticle"):
        pmid = _normalise_space(record.findtext("./MedlineCitation/PMID"))
        if not pmid:
            for article_id in record.findall(
                "./PubmedData/ArticleIdList/ArticleId"
            ):
                if article_id.attrib.get("IdType", "").casefold() == "pubmed":
                    pmid = _node_text(article_id)
                    break
        if not pmid.isdigit() or pmid in seen_pmids:
            continue

        article = record.find("./MedlineCitation/Article")
        title = _node_text(
            article.find("./ArticleTitle") if article is not None else None
        )
        abstract = _article_abstract_text(record, article)
        journal = ""
        if article is not None:
            journal = _normalise_space(article.findtext("./Journal/Title"))
        if not journal:
            journal = _normalise_space(
                article.findtext("./Journal/ISOAbbreviation")
                if article is not None
                else ""
            )
        if not journal:
            journal = _normalise_space(
                record.findtext("./MedlineCitation/MedlineJournalInfo/MedlineTA")
            )

        papers.append(
            PubMedPaper(
                pmid=pmid,
                title=title,
                abstract=abstract,
                journal=journal,
                year=_publication_year(record),
            )
        )
        seen_pmids.add(pmid)
    for paper in _parse_book_articles(root):
        if paper.pmid not in seen_pmids:
            papers.append(paper)
            seen_pmids.add(paper.pmid)
    return papers


def _retry_after_seconds(headers: object) -> float | None:
    if not isinstance(headers, Mapping):
        return None
    raw = headers.get("Retry-After")
    if raw is None:
        raw = headers.get("retry-after")
    value = _normalise_space(raw)
    if not value:
        return None
    try:
        return max(0.0, float(value))
    except ValueError:
        try:
            retry_at = parsedate_to_datetime(value)
            if retry_at.tzinfo is None:
                retry_at = retry_at.replace(tzinfo=timezone.utc)
            return max(
                0.0,
                (retry_at - datetime.now(timezone.utc)).total_seconds(),
            )
        except (TypeError, ValueError, OverflowError):
            return None


def _json_api_error(payload: bytes) -> str:
    if not payload.lstrip().startswith(b"{"):
        return ""
    try:
        parsed = json.loads(payload.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        return ""
    if not isinstance(parsed, Mapping):
        return ""
    return _normalise_space(parsed.get("error"))


class NCBIEUtilitiesClient:
    """Small, injectable client for PubMed ESearch and EFetch."""

    def __init__(
        self,
        *,
        session: Any = None,
        api_key: str | None = None,
        email: str | None = None,
        tool: str | None = None,
        timeout: float = 10.0,
        max_retries: int = 2,
        backoff_factor: float = 0.5,
        base_url: str = EUTILS_BASE_URL,
        sleeper: Callable[[float], None] | None = None,
        clock: Callable[[], float] | None = None,
        rate_limiter: RateLimiter | None = None,
    ) -> None:
        self.session = session if session is not None else requests.Session()
        self.api_key = _normalise_space(
            os.environ.get("PUBMED_API_KEY") if api_key is None else api_key
        )
        self.email = _normalise_space(
            os.environ.get("PUBMED_EMAIL") if email is None else email
        )
        if " " in self.email:
            self.email = ""
        self.tool = _normalise_space(
            os.environ.get("PUBMED_TOOL", "spatial_omics_copilot")
            if tool is None
            else tool
        ).replace(" ", "_") or "spatial_omics_copilot"
        self.timeout = max(0.1, float(timeout))
        self.max_retries = max(0, int(max_retries))
        self.backoff_factor = max(0.0, float(backoff_factor))
        self.base_url = base_url.rstrip("/") + "/"
        self._sleeper = sleeper if sleeper is not None else time.sleep

        if rate_limiter is not None:
            self._rate_limiter = rate_limiter
        elif sleeper is not None or clock is not None:
            self._rate_limiter = RateLimiter(
                10.0 if self.api_key else 3.0,
                sleeper=self._sleeper,
                clock=clock if clock is not None else time.monotonic,
            )
        else:
            self._rate_limiter = _shared_limiter(bool(self.api_key))

    def _common_params(self) -> dict[str, str]:
        params = {"db": "pubmed", "tool": self.tool}
        if self.email:
            params["email"] = self.email
        if self.api_key:
            params["api_key"] = self.api_key
        return params

    def close(self) -> None:
        closer = getattr(self.session, "close", None)
        if callable(closer):
            closer()

    def _request(self, endpoint: str, params: Mapping[str, object]) -> bytes:
        request_params: dict[str, object] = self._common_params()
        request_params.update(params)
        last_detail = "unknown request failure"

        for attempt in range(self.max_retries + 1):
            self._rate_limiter.wait()
            try:
                response = self.session.post(
                    self.base_url + endpoint,
                    data=request_params,
                    timeout=self.timeout,
                )
            except requests.RequestException as exc:
                last_detail = exc.__class__.__name__
                if attempt >= self.max_retries:
                    break
                self._sleeper(self.backoff_factor * (2**attempt))
                continue
            except (TimeoutError, OSError) as exc:
                # Lightweight test doubles may raise built-in timeout errors.
                last_detail = exc.__class__.__name__
                if attempt >= self.max_retries:
                    break
                self._sleeper(self.backoff_factor * (2**attempt))
                continue

            status_code = int(getattr(response, "status_code", 200))
            if 200 <= status_code < 300:
                content = getattr(response, "content", None)
                if content is None:
                    content = str(getattr(response, "text", "")).encode("utf-8")
                elif isinstance(content, str):
                    content = content.encode("utf-8")
                if not isinstance(content, bytes) or not content:
                    raise PubMedRequestError(
                        "NCBI returned an empty response body."
                    )
                api_error = _json_api_error(content)
                if api_error:
                    last_detail = "NCBI API error"
                    is_rate_limit = "rate limit" in api_error.casefold()
                    if is_rate_limit and attempt < self.max_retries:
                        self._sleeper(self.backoff_factor * (2**attempt))
                        continue
                    break
                return content

            last_detail = f"HTTP {status_code}"
            if (
                status_code not in _RETRYABLE_STATUS_CODES
                or attempt >= self.max_retries
            ):
                break

            retry_after = _retry_after_seconds(
                getattr(response, "headers", {})
            )
            if retry_after is not None and retry_after > 10.0:
                raise PubMedRequestError(
                    "NCBI requested a retry beyond the 10-second retry budget."
                )
            delay = (
                retry_after
                if retry_after is not None
                else self.backoff_factor * (2**attempt)
            )
            self._sleeper(max(delay, 0.0))

        raise PubMedRequestError(
            f"NCBI request failed after {self.max_retries + 1} attempt(s): "
            f"{last_detail}."
        )

    def search_ids(self, query: str, max_results: int = 5) -> list[str]:
        query_text = _normalise_space(query)
        limit = max(0, int(max_results))
        if not query_text or limit == 0:
            return []
        payload = self._request(
            "esearch.fcgi",
            {
                "term": query_text,
                "retmax": limit,
                "retmode": "xml",
                "sort": "relevance",
            },
        )
        return parse_esearch_xml(payload)[:limit]

    def fetch_articles(self, pmids: Iterable[object]) -> list[PubMedPaper]:
        unique_ids: list[str] = []
        seen: set[str] = set()
        for value in pmids:
            pmid = _normalise_space(value)
            if pmid.isdigit() and pmid not in seen:
                seen.add(pmid)
                unique_ids.append(pmid)
        if not unique_ids:
            return []

        payload = self._request(
            "efetch.fcgi",
            {
                "id": ",".join(unique_ids),
                "rettype": "abstract",
                "retmode": "xml",
            },
        )
        papers = parse_pubmed_xml(payload)
        by_pmid = {paper.pmid: paper for paper in papers}
        return [by_pmid[pmid] for pmid in unique_ids if pmid in by_pmid]


__all__ = [
    "EUTILS_BASE_URL",
    "NCBIEUtilitiesClient",
    "PubMedClientError",
    "PubMedParseError",
    "PubMedRequestError",
    "RateLimiter",
    "parse_esearch_xml",
    "parse_pubmed_xml",
]
