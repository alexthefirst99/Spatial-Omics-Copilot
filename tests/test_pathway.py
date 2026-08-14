from __future__ import annotations

import pandas as pd
import requests

from rag.pathway_enrichment import PathwayResult, run_pathway_enrichment
from rag.pathway_enrichment.enrichment import _fetch_enrichr_frames


def install_fake_enrichr(monkeypatch, fake_fetch):
    monkeypatch.setattr(
        "rag.pathway_enrichment.enrichment._fetch_enrichr_frames", fake_fetch
    )


def test_successful_enrichment_parses_sorts_filters_and_preserves_source(monkeypatch):
    calls = []

    def fake_fetch(genes, gene_sets, *, timeout, max_retries):
        calls.append(
            {
                "genes": genes,
                "gene_sets": gene_sets,
                "timeout": timeout,
                "max_retries": max_retries,
            }
        )
        return (
            [
                pd.DataFrame(
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
            ],
            [],
        )

    install_fake_enrichr(monkeypatch, fake_fetch)
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
    # The gene list is submitted once, then both libraries are fetched.
    assert len(calls) == 1
    assert calls[0]["genes"] == ["EPCAM", "KRAS", "TP53"]
    assert set(calls[0]["gene_sets"]) == {
        "GO_Biological_Process_2023",
        "KEGG_2021_Human",
    }
    assert calls[0]["timeout"] == 30
    assert calls[0]["max_retries"] == 2


def test_empty_gene_list_returns_safe_result_without_calling_enrichr(monkeypatch):
    def unexpected_fetch(*args, **kwargs):
        raise AssertionError("Enrichr should not be called for an empty gene list")

    install_fake_enrichr(monkeypatch, unexpected_fetch)

    result = run_pathway_enrichment([])

    assert result.pathways == []
    assert not result.ok
    assert result.status_message == "No genes provided for pathway enrichment."


def test_enrichr_api_failure_returns_safe_empty_result(monkeypatch):
    def failed_fetch(genes, gene_sets, *, timeout, max_retries):
        return [], ["GO_Biological_Process_2023: temporary Enrichr outage"]

    install_fake_enrichr(monkeypatch, failed_fetch)
    result = run_pathway_enrichment(["EPCAM", "TP53"])

    assert result.pathways == []
    assert "unavailable" in result.status_message.lower()
    assert "temporary Enrichr outage" in result.status_message
    assert result.input_genes == ["EPCAM", "TP53"]


def test_no_significant_pathways_reports_cutoff(monkeypatch):
    def fake_fetch(genes, gene_sets, *, timeout, max_retries):
        return (
            [
                pd.DataFrame(
                    [
                        {
                            "Gene_set": "KEGG_2021_Human",
                            "Term": "Weak signal",
                            "Adjusted_P-value": 0.2,
                            "Genes": "TP53",
                        }
                    ]
                )
            ],
            [],
        )

    install_fake_enrichr(monkeypatch, fake_fetch)
    result = run_pathway_enrichment(["TP53"])

    assert result.pathways == []
    assert "none passed" in result.status_message.lower()
    assert "0.05" in result.status_message


def test_https_client_falls_back_to_json_when_export_is_rejected(monkeypatch):
    class FakeResponse:
        def __init__(self, *, status=200, text="", payload=None):
            self.status_code = status
            self.text = text
            self._payload = payload

        def raise_for_status(self):
            if self.status_code >= 400:
                raise requests.HTTPError(f"status {self.status_code}")

        def json(self):
            return self._payload

    class FakeSession:
        def __init__(self):
            self.calls = []

        def mount(self, prefix, adapter):
            assert prefix == "https://"

        def close(self):
            pass

        def post(self, url, **kwargs):
            self.calls.append(("POST", url, kwargs))
            return FakeResponse(payload={"userListId": 123})

        def get(self, url, **kwargs):
            self.calls.append(("GET", url, kwargs))
            if url.endswith("/export"):
                return FakeResponse(status=502)
            return FakeResponse(
                payload={
                    "GO_Biological_Process_2023": [
                        [
                            1,
                            "Cell proliferation",
                            0.001,
                            5.0,
                            20.0,
                            ["EPCAM", "TP53"],
                            0.01,
                            0,
                            0,
                        ]
                    ]
                }
            )

    session = FakeSession()
    monkeypatch.setattr(
        "rag.pathway_enrichment.enrichment.requests.Session", lambda: session
    )

    frames, errors = _fetch_enrichr_frames(
        ["EPCAM", "TP53"],
        ["GO_Biological_Process_2023"],
        timeout=7,
        max_retries=1,
    )

    assert errors == []
    assert len(frames) == 1
    assert frames[0].iloc[0]["Gene_set"] == "GO_Biological_Process_2023"
    assert frames[0].iloc[0]["Genes"] == "EPCAM;TP53"
    assert [call[1] for call in session.calls] == [
        "https://maayanlab.cloud/Enrichr/addList",
        "https://maayanlab.cloud/Enrichr/export",
        "https://maayanlab.cloud/Enrichr/enrich",
    ]
    assert all(call[2]["timeout"] == 7 for call in session.calls)


def test_successful_empty_response_is_not_reported_as_api_failure(monkeypatch):
    def empty_fetch(genes, gene_sets, *, timeout, max_retries):
        return [pd.DataFrame(columns=["Gene_set", "Term", "Adjusted P-value"])], []

    install_fake_enrichr(monkeypatch, empty_fetch)

    result = run_pathway_enrichment(["UNKNOWN_GENE"])

    assert result.pathways == []
    assert "unavailable" not in result.status_message.lower()
    assert result.status_message == "No enriched pathways were returned by Enrichr."
