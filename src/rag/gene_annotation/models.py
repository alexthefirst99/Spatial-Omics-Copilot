"""Result models for NCBI Gene annotation retrieval."""

from __future__ import annotations

from collections.abc import Iterator, Mapping
from dataclasses import dataclass, field
from typing import Any


def _clean_text(value: object) -> str:
    if value is None:
        return ""
    return " ".join(str(value).split())


def _clean_aliases(values: object) -> tuple[str, ...]:
    if isinstance(values, str):
        values = values.replace(";", ",").split(",")
    try:
        candidates = list(values)  # type: ignore[arg-type]
    except TypeError:
        candidates = []

    aliases: list[str] = []
    seen: set[str] = set()
    for value in candidates:
        alias = _clean_text(value)
        if alias and alias.casefold() not in seen:
            seen.add(alias.casefold())
            aliases.append(alias)
    return tuple(aliases)


@dataclass(frozen=True, slots=True)
class GeneAnnotation(Mapping[str, Any]):
    """A single gene-level annotation with source provenance."""

    gene_symbol: str
    full_name: str = ""
    aliases: tuple[str, ...] = ()
    functional_summary: str = ""
    organism: str = ""
    source_database: str = "NCBI Gene"
    source_id: str = ""
    source_url: str = ""
    query_symbol: str = ""

    def __post_init__(self) -> None:
        object.__setattr__(self, "gene_symbol", _clean_text(self.gene_symbol))
        object.__setattr__(self, "full_name", _clean_text(self.full_name))
        object.__setattr__(self, "aliases", _clean_aliases(self.aliases))
        object.__setattr__(self, "functional_summary", _clean_text(self.functional_summary))
        object.__setattr__(self, "organism", _clean_text(self.organism))
        object.__setattr__(
            self,
            "source_database",
            _clean_text(self.source_database) or "NCBI Gene",
        )
        object.__setattr__(self, "source_id", _clean_text(self.source_id))
        object.__setattr__(self, "source_url", _clean_text(self.source_url))
        object.__setattr__(self, "query_symbol", _clean_text(self.query_symbol))

    def to_dict(self) -> dict[str, Any]:
        return {
            "gene_symbol": self.gene_symbol,
            "full_name": self.full_name,
            "aliases": list(self.aliases),
            "functional_summary": self.functional_summary,
            "organism": self.organism,
            "source_database": self.source_database,
            "source_id": self.source_id,
            "source_url": self.source_url,
            "query_symbol": self.query_symbol,
        }

    def __getitem__(self, key: str) -> Any:
        return self.to_dict()[key]

    def __iter__(self) -> Iterator[str]:
        return iter(self.to_dict())

    def __len__(self) -> int:
        return len(self.to_dict())


@dataclass(slots=True)
class GeneAnnotationResult:
    """Safe result envelope for top-ROI gene annotations."""

    genes: list[GeneAnnotation] = field(default_factory=list)
    source_database: str = "NCBI Gene"
    status_message: str = ""
    missing_genes: list[str] = field(default_factory=list)

    def __post_init__(self) -> None:
        normalised: list[GeneAnnotation] = []
        for item in list(self.genes or []):
            if isinstance(item, GeneAnnotation):
                normalised.append(item)
            elif isinstance(item, Mapping):
                normalised.append(
                    GeneAnnotation(
                        gene_symbol=item.get("gene_symbol", item.get("symbol", "")),
                        full_name=item.get("full_name", item.get("name", "")),
                        aliases=tuple(item.get("aliases", [])),
                        functional_summary=item.get(
                            "functional_summary", item.get("summary", "")
                        ),
                        organism=item.get("organism", ""),
                        source_database=item.get("source_database", "NCBI Gene"),
                        source_id=item.get("source_id", ""),
                        source_url=item.get("source_url", item.get("url", "")),
                        query_symbol=item.get("query_symbol", ""),
                    )
                )
        self.genes = normalised
        self.source_database = _clean_text(self.source_database) or "NCBI Gene"
        self.status_message = _clean_text(self.status_message)

        missing: list[str] = []
        seen: set[str] = set()
        for value in list(self.missing_genes or []):
            gene = _clean_text(value).upper()
            if gene and gene not in seen:
                seen.add(gene)
                missing.append(gene)
        self.missing_genes = missing

    @property
    def ok(self) -> bool:
        return bool(self.genes)

    @property
    def source_ids_or_urls(self) -> list[str]:
        return [
            gene.source_url or gene.source_id
            for gene in self.genes
            if gene.source_url or gene.source_id
        ]

    def to_dict(self) -> dict[str, Any]:
        return {
            "genes": [gene.to_dict() for gene in self.genes],
            "source_database": self.source_database,
            "source_ids_or_urls": self.source_ids_or_urls,
            "status_message": self.status_message,
            "missing_genes": list(self.missing_genes),
        }


__all__ = ["GeneAnnotation", "GeneAnnotationResult"]
