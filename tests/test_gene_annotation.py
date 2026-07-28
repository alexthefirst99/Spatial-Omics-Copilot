from __future__ import annotations

from dataclasses import dataclass

import requests

from rag.gene_annotation import (
    GeneAnnotation,
    GeneAnnotationResult,
    NCBIGeneClient,
    parse_esearch_json,
    parse_esummary_json,
    run_gene_annotation_retrieval,
)


ESEARCH_TP53 = {"esearchresult": {"idlist": ["7157"]}}
ESEARCH_EMPTY = {"esearchresult": {"idlist": []}}
ESUMMARY_TP53 = {
    "result": {
        "uids": ["7157"],
        "7157": {
            "uid": "7157",
            "name": "TP53",
            "description": "tumor protein p53",
            "nomenclaturesymbol": "TP53",
            "nomenclaturename": "tumor protein p53",
            "otheraliases": "BCC7, LFS1, P53, TRP53",
            "summary": (
                "This gene encodes a tumor suppressor protein involved in "
                "cell-cycle control and apoptosis."
            ),
            "organism": {
                "scientificname": "Homo sapiens",
                "commonname": "human",
                "taxid": 9606,
            },
        },
    }
}


@dataclass
class FakeResponse:
    payload: object
    status_code: int = 200
    headers: dict | None = None

    def __post_init__(self):
        if self.headers is None:
            self.headers = {}

    def json(self):
        if isinstance(self.payload, BaseException):
            raise self.payload
        return self.payload


class FakeSession:
    def __init__(self, *responses):
        self.responses = list(responses)
        self.calls = []

    def post(self, url, *, data, timeout):
        self.calls.append({"url": url, "data": dict(data), "timeout": timeout})
        response = self.responses.pop(0)
        if isinstance(response, BaseException):
            raise response
        return response


def test_parse_ncbi_gene_search_and_summary():
    assert parse_esearch_json(ESEARCH_TP53) == ["7157"]
    annotations = parse_esummary_json(ESUMMARY_TP53)

    annotation = annotations["7157"]
    assert annotation.gene_symbol == "TP53"
    assert annotation.full_name == "tumor protein p53"
    assert annotation.aliases == ("BCC7", "LFS1", "P53", "TRP53")
    assert "tumor suppressor" in annotation.functional_summary
    assert annotation.organism == "Homo sapiens"
    assert annotation.source_id == "7157"
    assert annotation.source_url.endswith("/gene/7157")


def test_successful_annotation_retrieval_and_missing_gene_are_safe():
    session = FakeSession(
        FakeResponse(ESEARCH_TP53),
        FakeResponse(ESUMMARY_TP53),
    )
    client = NCBIGeneClient(session=session, max_retries=0, email="test@example.org")

    result = run_gene_annotation_retrieval(
        ["tp53", "NOTAREALGENE", "TP53"],
        client=client,
    )

    assert isinstance(result, GeneAnnotationResult)
    assert result.ok
    assert len(result.genes) == 1
    assert result.genes[0]["gene_symbol"] == "TP53"
    assert result.genes[0]["query_symbol"] == "TP53"
    assert result.source_database == "NCBI Gene"
    assert result.source_ids_or_urls == ["https://www.ncbi.nlm.nih.gov/gene/7157"]
    assert result.missing_genes == ["NOTAREALGENE"]
    assert session.calls[0]["data"]["db"] == "gene"
    assert '"TP53"[sym]' in session.calls[0]["data"]["term"]
    assert '"NOTAREALGENE"[sym]' in session.calls[0]["data"]["term"]
    assert len(session.calls) == 2
    assert session.calls[-1]["url"].endswith("esummary.fcgi")
    assert session.calls[-1]["data"]["id"] == "7157"


def test_empty_and_invalid_inputs_return_without_network_calls():
    empty = run_gene_annotation_retrieval([])
    invalid = run_gene_annotation_retrieval(['TP53"] OR cancer[Title'])

    assert empty.genes == []
    assert empty.status_message == "No genes provided for annotation retrieval."
    assert invalid.genes == []
    assert invalid.missing_genes == ['TP53"] OR CANCER[TITLE']
    assert "No valid" in invalid.status_message


def test_timeout_is_contained_as_safe_result():
    session = FakeSession(requests.Timeout("slow"))
    client = NCBIGeneClient(session=session, max_retries=0)

    result = run_gene_annotation_retrieval(["TP53"], client=client)

    assert result.genes == []
    assert result.missing_genes == ["TP53"]
    assert "unavailable" in result.status_message.lower()


def test_esummary_failure_does_not_forward_partial_unverified_data():
    session = FakeSession(
        FakeResponse(ESEARCH_TP53),
        FakeResponse({}, status_code=503),
    )
    client = NCBIGeneClient(session=session, max_retries=0)

    result = run_gene_annotation_retrieval(["TP53"], client=client)

    assert result.genes == []
    assert result.missing_genes == ["TP53"]
    assert "fetching summaries" in result.status_message
