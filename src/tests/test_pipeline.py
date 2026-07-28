from __future__ import annotations

from rag.pipeline import _run_sequential


def _fake_abstracts(genes, pathways=None, n=3):
    return [
        {
            "pmid": str(1000 + index),
            "title": f"PubMed paper {index + 1}",
            "journal": "Test Journal",
            "year": 2024,
            "snippet": "A mocked abstract used to isolate the pipeline test.",
        }
        for index in range(n)
    ]


def _fake_pathways(genes, top_n=6):
    return [
        {
            "name": f"TEST · Pathway {index + 1}",
            "gene_count": min(2, len(genes)),
            "set_size": 50 + index,
            "pvalue": 0.001 * (index + 1),
            "overlap": list(genes[:2]),
        }
        for index in range(top_n)
    ]


def test_run_sequential_builds_complete_rag_metadata(monkeypatch):
    monkeypatch.setattr("rag.pipeline.retrieve_abstracts", _fake_abstracts)
    monkeypatch.setattr("rag.pipeline.enrich_pathways", _fake_pathways)
    gene_objects = [
        {"gene": "SNAP25", "log2_fold_change": 3.4},
        {"gene": "SYP", "log2_fold_change": 2.8},
        {"gene": "GRIA1", "log2_fold_change": 2.1},
    ]

    result = _run_sequential(
        gene_objects,
        message="What does this region suggest?",
        label="ROI 1",
        n_pathways=3,
        n_abstracts=2,
    )

    assert result["gene_objects"] == gene_objects
    assert result["context_str"].startswith("\n\nRAG-retrieved biological context for ROI 1")

    metadata = result["metadata"]
    assert metadata["label"] == "ROI 1"
    assert [step["step"] for step in metadata["trace"]] == [
        "Extracted top DEGs",
        "Pathway enrichment",
        "Retrieved 2 PubMed abstracts",
    ]
    assert [deg["gene"] for deg in metadata["degs"]] == ["SNAP25", "SYP", "GRIA1"]
    assert len(metadata["pathways"]) == 3
    assert len(metadata["citations"]) == 2
    assert metadata["citations"][0]["id"] == 1


def test_run_sequential_uses_demo_fallback_for_empty_gene_objects(monkeypatch):
    monkeypatch.setattr("rag.pipeline.retrieve_abstracts", _fake_abstracts)
    monkeypatch.setattr("rag.pipeline.enrich_pathways", _fake_pathways)
    result = _run_sequential([], n_pathways=2, n_abstracts=1)

    assert result["metadata"]["label"] == "demo"
    assert result["gene_objects"]
    assert result["metadata"]["trace"][0]["detail"].endswith("genes · demo")
