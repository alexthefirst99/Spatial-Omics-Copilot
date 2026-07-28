"""Result models for pathway enrichment.

The mapping interface intentionally supports both attribute access and the
``pathway[\"name\"]`` style used by the integration pipeline.
"""

from __future__ import annotations

from collections.abc import Iterator, Mapping
from dataclasses import dataclass, field
from math import isfinite
from typing import Any


def _clean_text(value: object) -> str:
    if value is None:
        return ""
    return " ".join(str(value).split())


def _clean_float(value: object) -> float | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if isfinite(number) else None


def _clean_genes(values: object) -> list[str]:
    if isinstance(values, str):
        values = values.replace(",", ";").split(";")
    try:
        candidates = list(values)  # type: ignore[arg-type]
    except TypeError:
        candidates = []

    genes: list[str] = []
    seen: set[str] = set()
    for value in candidates:
        gene = _clean_text(value).upper()
        if gene and gene not in seen:
            seen.add(gene)
            genes.append(gene)
    return genes


@dataclass(frozen=True, slots=True)
class PathwayEntry(Mapping[str, Any]):
    """One Enrichr pathway hit with explicit provenance."""

    name: str
    adjusted_p_value: float
    overlap_genes: tuple[str, ...] = ()
    source: str = ""
    nominal_p_value: float | None = None
    odds_ratio: float | None = None
    combined_score: float | None = None
    overlap_count: int | None = None
    gene_set_size: int | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "name", _clean_text(self.name))
        object.__setattr__(self, "source", _clean_text(self.source))
        adjusted = _clean_float(self.adjusted_p_value)
        object.__setattr__(self, "adjusted_p_value", adjusted if adjusted is not None else 1.0)
        object.__setattr__(self, "nominal_p_value", _clean_float(self.nominal_p_value))
        object.__setattr__(self, "odds_ratio", _clean_float(self.odds_ratio))
        object.__setattr__(self, "combined_score", _clean_float(self.combined_score))
        object.__setattr__(self, "overlap_genes", tuple(_clean_genes(self.overlap_genes)))

        overlap_count = self.overlap_count
        if overlap_count is None:
            overlap_count = len(self.overlap_genes)
        try:
            overlap_count = max(0, int(overlap_count))
        except (TypeError, ValueError):
            overlap_count = len(self.overlap_genes)
        object.__setattr__(self, "overlap_count", overlap_count)

        try:
            gene_set_size = int(self.gene_set_size) if self.gene_set_size is not None else None
        except (TypeError, ValueError):
            gene_set_size = None
        object.__setattr__(
            self,
            "gene_set_size",
            gene_set_size if gene_set_size and gene_set_size > 0 else None,
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "adjusted_p_value": self.adjusted_p_value,
            "overlap_genes": list(self.overlap_genes),
            "source": self.source,
            "nominal_p_value": self.nominal_p_value,
            "odds_ratio": self.odds_ratio,
            "combined_score": self.combined_score,
            "overlap_count": self.overlap_count,
            "gene_set_size": self.gene_set_size,
        }

    def _mapping(self) -> dict[str, Any]:
        data = self.to_dict()
        # Legacy aliases keep the current UI/pipeline working until Person 6
        # switches all callers to the shared contracts.
        data.update(
            {
                "pvalue": self.adjusted_p_value,
                "overlap": list(self.overlap_genes),
                "gene_count": self.overlap_count,
                "set_size": self.gene_set_size,
            }
        )
        return data

    def __getitem__(self, key: str) -> Any:
        return self._mapping()[key]

    def __iter__(self) -> Iterator[str]:
        return iter(self.to_dict())

    def __len__(self) -> int:
        return len(self.to_dict())


@dataclass(slots=True)
class PathwayResult:
    """Safe envelope for successful, empty, or failed Enrichr calls."""

    pathways: list[PathwayEntry] = field(default_factory=list)
    status_message: str = ""
    input_genes: list[str] = field(default_factory=list)
    sources: list[str] = field(default_factory=list)

    def __post_init__(self) -> None:
        normalised: list[PathwayEntry] = []
        for item in list(self.pathways or []):
            if isinstance(item, PathwayEntry):
                normalised.append(item)
            elif isinstance(item, Mapping):
                normalised.append(
                    PathwayEntry(
                        name=item.get("name", item.get("term", "")),
                        adjusted_p_value=item.get(
                            "adjusted_p_value", item.get("pvalue", 1.0)
                        ),
                        overlap_genes=tuple(
                            item.get("overlap_genes", item.get("overlap", []))
                        ),
                        source=item.get("source", ""),
                        nominal_p_value=item.get("nominal_p_value"),
                        odds_ratio=item.get("odds_ratio"),
                        combined_score=item.get("combined_score"),
                        overlap_count=item.get("overlap_count", item.get("gene_count")),
                        gene_set_size=item.get("gene_set_size", item.get("set_size")),
                    )
                )
        self.pathways = normalised
        self.status_message = _clean_text(self.status_message)
        self.input_genes = _clean_genes(self.input_genes)

        source_values = list(self.sources or []) + [p.source for p in self.pathways]
        self.sources = []
        seen: set[str] = set()
        for value in source_values:
            source = _clean_text(value)
            if source and source not in seen:
                seen.add(source)
                self.sources.append(source)

    @property
    def ok(self) -> bool:
        return bool(self.pathways)

    def to_dict(self) -> dict[str, Any]:
        return {
            "pathways": [pathway.to_dict() for pathway in self.pathways],
            "status_message": self.status_message,
            "input_genes": list(self.input_genes),
            "sources": list(self.sources),
        }


__all__ = ["PathwayEntry", "PathwayResult"]
