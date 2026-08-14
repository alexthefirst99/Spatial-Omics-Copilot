"""Build focused PubMed queries from ROI genes and enriched pathways."""

from __future__ import annotations

from collections.abc import Iterable


def _terms(values: Iterable[object] | None) -> list[str]:
    """Clean and de-duplicate user-derived terms while preserving order."""

    if values is None or isinstance(values, (str, bytes)):
        values = [values] if values else []

    cleaned: list[str] = []
    seen: set[str] = set()
    try:
        iterator = iter(values)
    except TypeError:
        return cleaned

    for value in iterator:
        if value is None:
            continue
        # A literal quote would terminate the PubMed phrase. Removing query
        # punctuation also prevents a pathway label from injecting field tags.
        term = str(value).replace('"', " ").replace("[", " ").replace("]", " ")
        term = " ".join(term.split())[:200].strip()
        key = term.casefold()
        if term and key not in seen:
            seen.add(key)
            cleaned.append(term)
    return cleaned


def _or_clause(terms: list[str], field: str = "Title/Abstract") -> str:
    return "(" + " OR ".join(f'"{term}"[{field}]' for term in terms) + ")"


def _pathway_labels(values: Iterable[object] | None) -> list[str]:
    labels: list[str] = []
    seen: set[str] = set()
    for term in _terms(values):
        if " · " in term:
            source, label = term.split(" · ", 1)
            if source.upper().startswith(("GO", "KEGG", "REACTOME")):
                term = label.strip()
        key = term.casefold()
        if term and key not in seen:
            labels.append(term)
            seen.add(key)
    return labels


def build_pubmed_query(
    genes: Iterable[object] | None,
    pathways: Iterable[object] | None = None,
    disease: str = "colorectal cancer",
) -> str:
    """Combine gene symbols, pathway names, and a disease context.

    Gene symbols and pathway names are searched in titles/abstracts. They are
    grouped as alternative evidence terms, then constrained by the disease
    context and PubMed's abstract-availability filter. This avoids requiring a
    relevant gene paper to repeat the exact pathway label returned by Enrichr.
    Empty groups are omitted rather than producing invalid ``()`` clauses.
    """

    # Keep the query focused on the highest-ranked upstream evidence. Broadly
    # OR-ing twenty-five ROI genes makes ESearch drift toward generic disease
    # papers that happen to mention one weak tail gene. A larger candidate pool
    # is fetched downstream, so precision here is more useful than URL breadth.
    gene_terms = _terms(genes)[:10]
    pathway_terms = _pathway_labels(pathways)[:5]
    disease_terms = _terms([disease])

    clauses: list[str] = []
    evidence_terms = [*gene_terms, *pathway_terms]
    if evidence_terms:
        clauses.append(_or_clause(evidence_terms))
    if disease_terms:
        clauses.append(_or_clause(disease_terms))
    if clauses:
        clauses.append("hasabstract")
    return " AND ".join(clauses)


__all__ = ["build_pubmed_query"]
