"""Retrieve trustworthy gene annotations from NCBI Gene."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
import os
import re
from typing import Any

from .client import NCBIGeneClient
from .models import GeneAnnotation, GeneAnnotationResult

_VALID_SYMBOL = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]*$")


def _config_value(config: object, dotted_keys: Sequence[str], default: Any) -> Any:
    for dotted_key in dotted_keys:
        current = config
        found = True
        for part in dotted_key.split("."):
            if isinstance(current, Mapping):
                if part not in current:
                    found = False
                    break
                current = current[part]
            else:
                if not hasattr(current, part):
                    found = False
                    break
                current = getattr(current, part)
        if found and current is not None:
            return current
    return default


def _normalise_genes(genes: object, *, max_genes: int) -> tuple[list[str], list[str]]:
    if isinstance(genes, str):
        genes = [genes]
    try:
        candidates = list(genes)  # type: ignore[arg-type]
    except TypeError:
        candidates = []

    valid: list[str] = []
    invalid: list[str] = []
    seen: set[str] = set()
    for value in candidates:
        symbol = " ".join(str(value).split()).upper() if value is not None else ""
        if not symbol or symbol in seen:
            continue
        seen.add(symbol)
        if not _VALID_SYMBOL.fullmatch(symbol):
            invalid.append(symbol)
            continue
        valid.append(symbol)
        if len(valid) >= max_genes:
            break
    return valid, invalid


def _build_client(config: object | None) -> NCBIGeneClient:
    return NCBIGeneClient(
        timeout=float(
            _config_value(
                config,
                ("gene_annotation.timeout", "ncbi.timeout", "timeout"),
                15.0,
            )
        ),
        max_retries=int(
            _config_value(
                config,
                ("gene_annotation.max_retries", "ncbi.max_retries", "max_retries"),
                2,
            )
        ),
        api_key=str(
            _config_value(
                config,
                ("gene_annotation.api_key", "ncbi.api_key"),
                os.getenv("NCBI_API_KEY", os.getenv("PUBMED_API_KEY", "")),
            )
        ),
        email=str(
            _config_value(
                config,
                ("gene_annotation.email", "ncbi.email"),
                os.getenv("NCBI_EMAIL", os.getenv("PUBMED_EMAIL", "")),
            )
        ),
        tool=str(
            _config_value(
                config,
                ("gene_annotation.tool", "ncbi.tool"),
                "spatial_omics_copilot",
            )
        ),
    )


def _matching_queries(
    annotation: GeneAnnotation,
    query_genes: list[str],
) -> list[str]:
    identifiers = {annotation.gene_symbol.casefold()}
    identifiers.update(alias.casefold() for alias in annotation.aliases)
    return [gene for gene in query_genes if gene.casefold() in identifiers]


def run_gene_annotation_retrieval(
    genes: list[str],
    config: object | None = None,
    *,
    client: NCBIGeneClient | None = None,
) -> GeneAnnotationResult:
    """Retrieve annotations for top ROI genes from NCBI Gene.

    Symbols are resolved in one ESearch request and their document summaries are
    fetched in one ESummary request. Invalid, missing, timeout, and parse cases
    return a safe result rather than escaping into the app worker.
    """

    max_genes = max(
        1,
        int(
            _config_value(
                config,
                ("gene_annotation.max_genes", "max_genes"),
                10,
            )
        ),
    )
    organism = str(
        _config_value(
            config,
            ("gene_annotation.organism", "organism"),
            "Homo sapiens",
        )
    )
    query_genes, invalid_genes = _normalise_genes(genes, max_genes=max_genes)
    if not query_genes and not invalid_genes:
        return GeneAnnotationResult(
            genes=[],
            source_database="NCBI Gene",
            status_message="No genes provided for annotation retrieval.",
            missing_genes=[],
        )
    if not query_genes:
        return GeneAnnotationResult(
            genes=[],
            source_database="NCBI Gene",
            status_message="No valid gene symbols were provided for annotation retrieval.",
            missing_genes=invalid_genes,
        )

    ncbi_client = client or _build_client(config)
    try:
        if hasattr(ncbi_client, "search_gene_ids"):
            gene_ids = ncbi_client.search_gene_ids(query_genes, organism=organism)
        else:  # pragma: no cover - compatibility for minimal external clients.
            gene_ids = []
            for symbol in query_genes:
                gene_id = ncbi_client.search_gene_id(symbol, organism=organism)
                if gene_id and gene_id not in gene_ids:
                    gene_ids.append(gene_id)
    except Exception:
        return GeneAnnotationResult(
            genes=[],
            source_database="NCBI Gene",
            status_message="NCBI Gene annotation retrieval was unavailable during symbol lookup.",
            missing_genes=invalid_genes + query_genes,
        )

    if not gene_ids:
        return GeneAnnotationResult(
            genes=[],
            source_database="NCBI Gene",
            status_message="No NCBI Gene records matched the requested symbols.",
            missing_genes=invalid_genes + query_genes,
        )

    try:
        summaries = ncbi_client.fetch_gene_summaries(gene_ids)
    except Exception:
        return GeneAnnotationResult(
            genes=[],
            source_database="NCBI Gene",
            status_message=(
                "NCBI Gene annotation retrieval was unavailable while fetching "
                "summaries."
            ),
            missing_genes=invalid_genes + query_genes,
        )

    annotations: list[GeneAnnotation] = []
    resolved_queries: set[str] = set()
    for gene_id in gene_ids:
        annotation = summaries.get(gene_id)
        if annotation is None:
            continue
        matches = _matching_queries(annotation, query_genes)
        if not matches:
            # Do not forward an unrelated record merely because ESearch ranked
            # it near an exact-symbol query.
            continue
        resolved_queries.update(matches)
        annotations.append(
            GeneAnnotation(
                gene_symbol=annotation.gene_symbol or matches[0],
                full_name=annotation.full_name,
                aliases=annotation.aliases,
                functional_summary=annotation.functional_summary,
                organism=annotation.organism or organism,
                source_database=annotation.source_database or "NCBI Gene",
                source_id=annotation.source_id or gene_id,
                source_url=annotation.source_url
                or f"https://www.ncbi.nlm.nih.gov/gene/{gene_id}",
                query_symbol=matches[0],
            )
        )

    missing = invalid_genes + [
        gene for gene in query_genes if gene not in resolved_queries
    ]
    if annotations and missing:
        status = (
            f"Retrieved {len(annotations)} gene annotation(s) from NCBI Gene; "
            f"{len(set(missing))} symbol(s) were missing or unavailable."
        )
    elif annotations:
        status = f"Retrieved {len(annotations)} gene annotation(s) from NCBI Gene."
    else:
        status = "No verified NCBI Gene annotations matched the requested symbols."

    return GeneAnnotationResult(
        genes=annotations,
        source_database="NCBI Gene",
        status_message=status,
        missing_genes=missing,
    )


__all__ = ["run_gene_annotation_retrieval"]
