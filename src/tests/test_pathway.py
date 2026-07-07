from __future__ import annotations

from rag.pathway import enrich_pathways


REQUIRED_KEYS = {"name", "gene_count", "set_size", "pvalue", "overlap"}


def test_enrich_pathways_returns_expected_schema_for_matching_genes():
    results = enrich_pathways(["snap25", "SYP", "GRIA1"], top_n=3)

    assert len(results) == 3
    assert REQUIRED_KEYS <= results[0].keys()
    assert results[0]["name"].startswith("GO:0007268")
    assert results[0]["gene_count"] == 3
    assert set(results[0]["overlap"]) == {"SNAP25", "SYP", "GRIA1"}
    assert 0 < results[0]["pvalue"] <= 0.99


def test_enrich_pathways_is_deterministic_for_same_input_order():
    genes = ["AIF1", "TREM2", "C1QA", "GFAP"]

    first = enrich_pathways(genes, top_n=4)
    second = enrich_pathways(genes, top_n=4)

    assert first == second


def test_enrich_pathways_empty_gene_list_returns_empty_list():
    assert enrich_pathways([], top_n=4) == []
