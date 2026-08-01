"""Shared result contracts for the preprocessing/clustering entry points.

``preprocess_h5ad()`` (src/rag/preprocessing.py) and ``cluster_adata()``
(src/rag/clustering.py) return these types instead of plain dicts so
downstream callers (the integration pipeline, tests) get a typed shape.
Call ``.to_dict()`` for the equivalent plain-dict form used by existing
tests and callers.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True, slots=True)
class PreprocessResult:
    """Outcome of ``preprocess_h5ad()`` — a cached, preprocessed AnnData file."""

    adata_path: str
    n_spots: int
    n_genes: int
    from_cache: bool = False

    def to_dict(self) -> dict[str, Any]:
        return {
            "adata_path": self.adata_path,
            "qc_summary": {"n_spots": self.n_spots, "n_genes": self.n_genes},
            "from_cache": self.from_cache,
        }


@dataclass(frozen=True, slots=True)
class ClusterResult:
    """Outcome of ``cluster_adata()`` — spatial cluster assignments for an AnnData file."""

    adata_path: str
    cluster_path: str
    method: str
    n_clusters: int
    n_spots: int

    def to_dict(self) -> dict[str, Any]:
        return {
            "adata_path": self.adata_path,
            "cluster_path": self.cluster_path,
            "cluster_summary": {
                "method": self.method,
                "n_clusters": self.n_clusters,
                "n_spots": self.n_spots,
            },
        }


__all__ = ["PreprocessResult", "ClusterResult"]
