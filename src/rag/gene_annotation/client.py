"""Small NCBI Gene ESearch/ESummary client with bounded retries."""

from __future__ import annotations

from collections.abc import Callable, Mapping
import os
import time
from typing import Any

import requests

from .models import GeneAnnotation

EUTILS_BASE_URL = "https://eutils.ncbi.nlm.nih.gov/entrez/eutils/"
_RETRYABLE_STATUS_CODES = {408, 425, 429, 500, 502, 503, 504}


class GeneAnnotationClientError(RuntimeError):
    """Base class for handled NCBI Gene failures."""


class GeneAnnotationRequestError(GeneAnnotationClientError):
    """Raised when an NCBI request cannot be completed safely."""


class GeneAnnotationParseError(GeneAnnotationClientError):
    """Raised when NCBI returns an unexpected JSON structure."""


def _clean_text(value: object) -> str:
    if value is None:
        return ""
    return " ".join(str(value).split())


def parse_esearch_json(payload: Mapping[str, Any]) -> list[str]:
    try:
        result = payload["esearchresult"]
        raw_ids = result.get("idlist", [])
    except (KeyError, AttributeError, TypeError) as exc:
        raise GeneAnnotationParseError("NCBI Gene ESearch returned malformed JSON.") from exc

    ids: list[str] = []
    seen: set[str] = set()
    for value in raw_ids:
        gene_id = _clean_text(value)
        if gene_id and not gene_id.isdigit():
            raise GeneAnnotationParseError("NCBI Gene ESearch returned an invalid Gene ID.")
        if gene_id and gene_id not in seen:
            seen.add(gene_id)
            ids.append(gene_id)
    return ids


def _split_aliases(value: object) -> tuple[str, ...]:
    text = _clean_text(value)
    if not text:
        return ()
    aliases: list[str] = []
    seen: set[str] = set()
    for item in text.replace(";", ",").split(","):
        alias = _clean_text(item)
        if alias and alias.casefold() not in seen:
            seen.add(alias.casefold())
            aliases.append(alias)
    return tuple(aliases)


def parse_esummary_json(payload: Mapping[str, Any]) -> dict[str, GeneAnnotation]:
    try:
        result = payload["result"]
    except (KeyError, TypeError) as exc:
        raise GeneAnnotationParseError("NCBI Gene ESummary returned malformed JSON.") from exc
    if not isinstance(result, Mapping):
        raise GeneAnnotationParseError("NCBI Gene ESummary returned malformed JSON.")

    raw_uids = result.get("uids", [])
    annotations: dict[str, GeneAnnotation] = {}
    for raw_uid in raw_uids:
        uid = _clean_text(raw_uid)
        item = result.get(uid)
        if not uid.isdigit() or not isinstance(item, Mapping):
            continue
        organism_data = item.get("organism", {})
        organism = ""
        if isinstance(organism_data, Mapping):
            organism = _clean_text(
                organism_data.get("scientificname", organism_data.get("commonname", ""))
            )
        symbol = _clean_text(
            item.get("nomenclaturesymbol", item.get("name", ""))
        )
        full_name = _clean_text(
            item.get("nomenclaturename", item.get("description", ""))
        )
        summary = _clean_text(item.get("summary", ""))
        if not summary:
            summary = _clean_text(item.get("description", ""))
        annotations[uid] = GeneAnnotation(
            gene_symbol=symbol,
            full_name=full_name,
            aliases=_split_aliases(item.get("otheraliases", "")),
            functional_summary=summary,
            organism=organism,
            source_database="NCBI Gene",
            source_id=uid,
            source_url=f"https://www.ncbi.nlm.nih.gov/gene/{uid}",
        )
    return annotations


class NCBIGeneClient:
    """HTTP client for exact symbol lookup in the NCBI Gene database."""

    def __init__(
        self,
        *,
        session: requests.Session | None = None,
        timeout: float = 15.0,
        max_retries: int = 2,
        backoff_factor: float = 0.5,
        api_key: str | None = None,
        email: str | None = None,
        tool: str = "spatial_omics_copilot",
        sleeper: Callable[[float], None] = time.sleep,
    ) -> None:
        self.session = session or requests.Session()
        self.timeout = max(0.1, float(timeout))
        self.max_retries = max(0, int(max_retries))
        self.backoff_factor = max(0.0, float(backoff_factor))
        self.api_key = _clean_text(
            api_key
            if api_key is not None
            else os.getenv("NCBI_API_KEY", os.getenv("PUBMED_API_KEY", ""))
        )
        self.email = _clean_text(
            email
            if email is not None
            else os.getenv("NCBI_EMAIL", os.getenv("PUBMED_EMAIL", ""))
        )
        self.tool = _clean_text(tool) or "spatial_omics_copilot"
        self.sleeper = sleeper

    def _request_json(self, endpoint: str, data: dict[str, Any]) -> Mapping[str, Any]:
        request_data = dict(data)
        request_data.setdefault("retmode", "json")
        request_data["tool"] = self.tool
        if self.email:
            request_data["email"] = self.email
        if self.api_key:
            request_data["api_key"] = self.api_key

        last_error = "unknown request failure"
        for attempt in range(self.max_retries + 1):
            try:
                response = self.session.post(
                    EUTILS_BASE_URL + endpoint,
                    data=request_data,
                    timeout=self.timeout,
                )
            except requests.RequestException as exc:
                last_error = _clean_text(exc) or exc.__class__.__name__
                if attempt < self.max_retries:
                    self.sleeper(self.backoff_factor * (2**attempt))
                    continue
                raise GeneAnnotationRequestError(last_error) from exc

            status_code = int(getattr(response, "status_code", 0))
            if status_code in _RETRYABLE_STATUS_CODES and attempt < self.max_retries:
                retry_after = None
                headers = getattr(response, "headers", {})
                if isinstance(headers, Mapping):
                    try:
                        retry_after = float(headers.get("Retry-After", ""))
                    except (TypeError, ValueError):
                        retry_after = None
                delay = (
                    retry_after
                    if retry_after is not None
                    else self.backoff_factor * (2**attempt)
                )
                self.sleeper(min(max(0.0, delay), 10.0))
                continue
            if status_code < 200 or status_code >= 300:
                raise GeneAnnotationRequestError(
                    f"NCBI Gene returned HTTP {status_code or 'error'}."
                )
            try:
                payload = response.json()
            except (ValueError, TypeError) as exc:
                raise GeneAnnotationParseError(
                    "NCBI Gene returned malformed JSON."
                ) from exc
            if not isinstance(payload, Mapping):
                raise GeneAnnotationParseError("NCBI Gene returned malformed JSON.")
            return payload

        raise GeneAnnotationRequestError(last_error)

    def search_gene_ids(
        self,
        symbols: list[str],
        *,
        organism: str = "Homo sapiens",
    ) -> list[str]:
        """Resolve several exact symbols with one ESearch request."""

        clean_symbols = [
            _clean_text(symbol) for symbol in symbols if _clean_text(symbol)
        ]
        if not clean_symbols:
            return []
        clean_organism = _clean_text(organism) or "Homo sapiens"
        symbol_query = " OR ".join(
            f'"{symbol}"[sym]' for symbol in clean_symbols
        )
        payload = self._request_json(
            "esearch.fcgi",
            {
                "db": "gene",
                "term": f'({symbol_query}) AND "{clean_organism}"[orgn]',
                "retmax": max(len(clean_symbols) * 2, 10),
            },
        )
        return parse_esearch_json(payload)

    def search_gene_id(self, symbol: str, *, organism: str = "Homo sapiens") -> str | None:
        """Backward-compatible single-symbol lookup."""

        ids = self.search_gene_ids([symbol], organism=organism)
        return ids[0] if ids else None

    def fetch_gene_summaries(self, gene_ids: list[str]) -> dict[str, GeneAnnotation]:
        ids = [str(value) for value in gene_ids if str(value).isdigit()]
        if not ids:
            return {}
        payload = self._request_json(
            "esummary.fcgi",
            {"db": "gene", "id": ",".join(ids), "version": "2.0"},
        )
        return parse_esummary_json(payload)


__all__ = [
    "EUTILS_BASE_URL",
    "GeneAnnotationClientError",
    "GeneAnnotationParseError",
    "GeneAnnotationRequestError",
    "NCBIGeneClient",
    "parse_esearch_json",
    "parse_esummary_json",
]
