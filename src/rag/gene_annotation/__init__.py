"""NCBI Gene annotation public API."""

from .client import (
    GeneAnnotationClientError,
    GeneAnnotationParseError,
    GeneAnnotationRequestError,
    NCBIGeneClient,
    parse_esearch_json,
    parse_esummary_json,
)
from .models import GeneAnnotation, GeneAnnotationResult
from .retrieval import run_gene_annotation_retrieval

__all__ = [
    "GeneAnnotation",
    "GeneAnnotationClientError",
    "GeneAnnotationParseError",
    "GeneAnnotationRequestError",
    "GeneAnnotationResult",
    "NCBIGeneClient",
    "parse_esearch_json",
    "parse_esummary_json",
    "run_gene_annotation_retrieval",
]
