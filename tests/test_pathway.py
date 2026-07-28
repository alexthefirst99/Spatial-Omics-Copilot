from __future__ import annotations

import sys
from types import SimpleNamespace

import pandas as pd

from rag.pathway_enrichment import PathwayResult, run_pathway_enrichment


def install_fake_gseapy(monkeypatch, fake_enrichr):
    monkeypatch.setitem(sys.modules, "gseapy", SimpleNamespace(enrichr=fake_enrichr))


def test_successful_enrichment_parses_sorts_filters_and_preserves_source(monkeypatch):
    calls = []

    def fake_enrichr(**kwargs):
        calls.append(kwargs)
        return SimpleNamespace(
            res2d=pd.DataFrame(
                [
                    {
                        "Gene_set": "KEGG_2021_Human",
                        "Term": "Colorectal cancer",
                        "Overlap": "2/86",
                        "P-value": 0.002,
                        "Adjusted P-value": 0.01,
                        "Odds Ratio": 8.2,
                        "Combined Score": 31.4,
                        "Genes": "TP53;KRAS",
                    },
                    {
                        "Gene_set": "GO_Biological_Process_2023",
                        "Term": "epithelial cell proliferation",
                        "Overlap": "3/120",
                        "P-value": 0.0001,
                        "Adjusted P-value": 0.004,
                        "Odds Ratio": 9.1,
                        "Combined Score": 42.0,
                        "Genes": "EPCAM;KRAS;TP53",
                    },
                    {
                        "Gene_set": "GO_Biological_Process_2023",
                        "Term": "non-significant term",
                        "Overlap": "1/300",
                        "P-value": 0.2,
                        "Adjusted P-value": 0.4,
                        "Genes": "EPCAM",
                    },
                ]
            )
        )

    install_fake_gseapy(monkeypatch, fake_enrichr)
    result = run_pathway_enrichment(
        ["epcam", "KRAS", "TP53", "EPCAM"],
        config={"pathway_enrichment": {"top_n": 5}},
    )

    assert isinstance(result, PathwayResult)
    assert result.ok
    assert [item.name for item in result.pathways] == [
        "epithelial cell proliferation",
        "Colorectal cancer",
    ]
    first = result.pathways[0]
    assert first["source"] == "GO_Biological_Process_2023"
    assert first["adjusted_p_value"] == 0.004
    assert first["pvalue"] == 0.004  # legacy alias
    assert first["overlap"] == ["EPCAM", "KRAS", "TP53"]
    assert first["gene_count"] == 3
    assert first["set_size"] == 120
    assert calls[0]["gene_list"] == ["EPCAM", "KRAS", "TP53"]
    assert calls[0]["gene_sets"] == [
        "GO_Biological_Process_2023",
        "KEGG_2021_Human",
    ]
    assert calls[0]["outdir"] is None
    assert calls[0]["no_plot"] is True


def test_empty_gene_list_returns_safe_result_without_importing_gseapy(monkeypatch):
    monkeypatch.delitem(sys.modules, "gseapy", raising=False)

    result = run_pathway_enrichment([])

    assert result.pathways == []
    assert not result.ok
    assert result.status_message == "No genes provided for pathway enrichment."


def test_enrichr_api_failure_returns_safe_empty_result(monkeypatch):
    def failed_enrichr(**kwargs):
        raise RuntimeError("temporary Enrichr outage")

    install_fake_gseapy(monkeypatch, failed_enrichr)
    result = run_pathway_enrichment(["EPCAM", "TP53"])

    assert result.pathways == []
    assert "unavailable" in result.status_message.lower()
    assert "temporary Enrichr outage" in result.status_message
    assert result.input_genes == ["EPCAM", "TP53"]


def test_no_significant_pathways_reports_cutoff(monkeypatch):
    def fake_enrichr(**kwargs):
        return SimpleNamespace(
            results=pd.DataFrame(
                [
                    {
                        "Gene_set": "KEGG_2021_Human",
                        "Term": "Weak signal",
                        "Adjusted_P-value": 0.2,
                        "Genes": "TP53",
                    }
                ]
            )
        )

    install_fake_gseapy(monkeypatch, fake_enrichr)
    result = run_pathway_enrichment(["TP53"])

    assert result.pathways == []
    assert "none passed" in result.status_message.lower()
    assert "0.05" in result.status_message
