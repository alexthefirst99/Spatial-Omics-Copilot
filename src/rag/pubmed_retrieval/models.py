"""Small, dependency-free result models for PubMed retrieval."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any


def _clean_text(value: object) -> str:
    """Return a predictable single-line string for PubMed metadata."""

    if value is None:
        return ""
    return " ".join(str(value).split())


def _clean_year(value: object) -> int | None:
    """Normalise a plausible publication year without rejecting a record."""

    if isinstance(value, bool) or value is None:
        return None
    try:
        year = int(value)
    except (TypeError, ValueError):
        return None
    return year if 1000 <= year <= 9999 else None


@dataclass(frozen=True, slots=True)
class PubMedPaper:
    """A literature record returned by PubMed."""

    pmid: str = ""
    title: str = ""
    abstract: str = ""
    journal: str = ""
    year: int | None = None

    def __post_init__(self) -> None:
        pmid = _clean_text(self.pmid)
        object.__setattr__(self, "pmid", pmid if pmid.isdigit() else "")
        object.__setattr__(self, "title", _clean_text(self.title))
        object.__setattr__(self, "abstract", _clean_text(self.abstract))
        object.__setattr__(self, "journal", _clean_text(self.journal))
        object.__setattr__(self, "year", _clean_year(self.year))

    @property
    def url(self) -> str:
        """Canonical PubMed URL, or an empty string when no PMID is present."""

        return f"https://pubmed.ncbi.nlm.nih.gov/{self.pmid}/" if self.pmid else ""

    def to_dict(self) -> dict[str, Any]:
        """Return a serialization-friendly copy of the public paper fields."""

        return {
            "pmid": self.pmid,
            "title": self.title,
            "abstract": self.abstract,
            "journal": self.journal,
            "year": self.year,
        }


@dataclass(slots=True)
class PubMedResult:
    """Safe result envelope used for both successful and failed retrievals."""

    papers: list[PubMedPaper] = field(default_factory=list)
    status_message: str = ""
    query: str = ""

    def __post_init__(self) -> None:
        # Copy the input list so two result instances can never share mutable
        # state through a caller-owned list.
        normalised: list[PubMedPaper] = []
        try:
            candidates = list(self.papers)
        except TypeError:
            candidates = []
        for paper in candidates:
            if isinstance(paper, PubMedPaper):
                normalised.append(paper)
            elif isinstance(paper, Mapping):
                normalised.append(
                    PubMedPaper(
                        pmid=paper.get("pmid", ""),
                        title=paper.get("title", ""),
                        abstract=paper.get("abstract", paper.get("snippet", "")),
                        journal=paper.get("journal", ""),
                        year=paper.get("year"),
                    )
                )
        self.papers = normalised
        self.status_message = _clean_text(self.status_message)
        self.query = _clean_text(self.query)

    @property
    def ok(self) -> bool:
        """Whether at least one real PubMed record was retrieved."""

        return bool(self.papers)

    def to_dict(self) -> dict[str, Any]:
        """Return a serialization-friendly result for workers and tests."""

        return {
            "papers": [paper.to_dict() for paper in self.papers],
            "status_message": self.status_message,
            "query": self.query,
        }


__all__ = ["PubMedPaper", "PubMedResult"]
