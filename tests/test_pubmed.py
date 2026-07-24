from __future__ import annotations

from dataclasses import dataclass

import pytest
import requests

from rag.pubmed_retrieval import (
    NCBIEUtilitiesClient,
    PubMedPaper,
    PubMedParseError,
    PubMedResult,
    build_pubmed_query,
    parse_esearch_xml,
    parse_pubmed_xml,
    retrieve_abstracts,
    search_pubmed,
    semantic_search_abstracts,
)


ESEARCH_TWO = b"""\
<eSearchResult>
  <Count>2</Count>
  <IdList><Id>111</Id><Id>222</Id><Id>111</Id></IdList>
</eSearchResult>
"""

ESEARCH_EMPTY = b"""\
<eSearchResult><Count>0</Count><IdList /></eSearchResult>
"""

EFETCH_TWO = b"""\
<PubmedArticleSet>
  <PubmedArticle>
    <MedlineCitation>
      <PMID Version="1">111</PMID>
      <Article>
        <ArticleTitle>Spatial <i>EPCAM</i> programs in colorectal cancer</ArticleTitle>
        <Abstract>
          <AbstractText Label="BACKGROUND">Tumours contain spatially distinct epithelial states.</AbstractText>
          <AbstractText Label="RESULTS">EPCAM-high regions showed a coherent expression program.</AbstractText>
        </Abstract>
        <Journal>
          <JournalIssue>
            <PubDate><MedlineDate>2024 Jan-Feb</MedlineDate></PubDate>
          </JournalIssue>
          <Title>Journal of Spatial Oncology</Title>
        </Journal>
      </Article>
    </MedlineCitation>
  </PubmedArticle>
  <PubmedArticle>
    <MedlineCitation>
      <PMID Version="1">222</PMID>
      <Article>
        <ArticleTitle>Wnt signalling in colorectal tumours</ArticleTitle>
        <Abstract>
          <AbstractText>Wnt activity was associated with tumour-cell states.</AbstractText>
        </Abstract>
        <ArticleDate><Year>2023</Year></ArticleDate>
        <Journal>
          <JournalIssue><PubDate /></JournalIssue>
          <ISOAbbreviation>CRC Res.</ISOAbbreviation>
        </Journal>
      </Article>
    </MedlineCitation>
  </PubmedArticle>
</PubmedArticleSet>
"""

EFETCH_EDGE_CASES = b"""\
<PubmedArticleSet>
  <PubmedArticle>
    <MedlineCitation>
      <PMID>333</PMID>
      <DateCompleted><Year>2025</Year></DateCompleted>
      <Article>
        <ArticleTitle>Article with an alternative abstract</ArticleTitle>
        <Journal><JournalIssue><PubDate /></JournalIssue></Journal>
      </Article>
      <OtherAbstract Type="Publisher" Language="spa">
        <AbstractText>Resumen en espanol.</AbstractText>
      </OtherAbstract>
      <OtherAbstract Type="plain-language-summary" Language="eng">
        <AbstractText>English plain-language abstract.</AbstractText>
      </OtherAbstract>
      <MedlineJournalInfo><MedlineTA>Medline Journal</MedlineTA></MedlineJournalInfo>
    </MedlineCitation>
    <PubmedData>
      <History><PubMedPubDate PubStatus="pubmed"><Year>2026</Year></PubMedPubDate></History>
    </PubmedData>
  </PubmedArticle>
  <PubmedBookArticle>
    <BookDocument>
      <PMID>444</PMID>
      <ArticleIdList />
      <Book>
        <Publisher><PublisherName>NCBI Bookshelf Publisher</PublisherName></Publisher>
        <BookTitle>Colorectal Cancer Biology</BookTitle>
        <PubDate><Year>2021</Year></PubDate>
      </Book>
      <ArticleTitle>SPP1 in the tumour microenvironment</ArticleTitle>
      <Abstract><AbstractText>Book chapter abstract.</AbstractText></Abstract>
    </BookDocument>
  </PubmedBookArticle>
</PubmedArticleSet>
"""


@dataclass
class FakeResponse:
    content: bytes = b""
    status_code: int = 200
    headers: dict | None = None

    def __post_init__(self) -> None:
        if self.headers is None:
            self.headers = {}


class FakeSession:
    def __init__(self, *responses: object) -> None:
        self.responses = list(responses)
        self.calls: list[dict] = []

    def post(self, url: str, *, data: dict, timeout: float) -> FakeResponse:
        self.calls.append(
            {
                "method": "POST",
                "url": url,
                "params": dict(data),
                "timeout": timeout,
            }
        )
        response = self.responses.pop(0)
        if isinstance(response, BaseException):
            raise response
        assert isinstance(response, FakeResponse)
        return response


class FakeClock:
    def __init__(self) -> None:
        self.value = 0.0
        self.sleeps: list[float] = []

    def now(self) -> float:
        return self.value

    def sleep(self, seconds: float) -> None:
        self.sleeps.append(seconds)
        self.value += seconds


def make_client(session: FakeSession, clock: FakeClock, **kwargs) -> NCBIEUtilitiesClient:
    return NCBIEUtilitiesClient(
        session=session,
        sleeper=clock.sleep,
        clock=clock.now,
        backoff_factor=0.1,
        **kwargs,
    )


def test_build_query_uses_genes_pathways_disease_and_abstract_filter():
    query = build_pubmed_query(
        genes=["EPCAM", "epcam", 'TP53"] OR cancer[Title'],
        pathways=["GO:0016055 · Wnt signaling pathway"],
    )

    assert query.count('"EPCAM"[Title/Abstract]') == 1
    assert '"TP53 OR cancer Title"[Title/Abstract]' in query
    assert '"Wnt signaling pathway"[Title/Abstract]' in query
    assert "GO:0016055" not in query
    assert '"colorectal cancer"[Title/Abstract]' in query
    assert query.endswith("hasabstract")
    assert "[Title] OR" not in query


def test_parse_esearch_ids_deduplicates_in_order():
    assert parse_esearch_xml(ESEARCH_TWO) == ["111", "222"]


def test_parse_esearch_rejects_field_errors_instead_of_broadening_query():
    payload = b"""\
    <eSearchResult>
      <IdList><Id>111</Id></IdList>
      <ErrorList><FieldNotFound>DefinitelyNotAField</FieldNotFound></ErrorList>
    </eSearchResult>
    """

    with pytest.raises(PubMedParseError, match="rejected part"):
        parse_esearch_xml(payload)


def test_parse_efetch_maps_nested_title_abstract_journal_and_year():
    papers = parse_pubmed_xml(EFETCH_TWO)

    assert [paper.pmid for paper in papers] == ["111", "222"]
    assert papers[0].title == "Spatial EPCAM programs in colorectal cancer"
    assert papers[0].abstract.startswith("BACKGROUND:")
    assert "RESULTS:" in papers[0].abstract
    assert papers[0].journal == "Journal of Spatial Oncology"
    assert papers[0].year == 2024
    assert papers[1].journal == "CRC Res."
    assert papers[1].year == 2023


def test_parse_efetch_supports_other_abstracts_books_and_true_publication_years():
    papers = parse_pubmed_xml(EFETCH_EDGE_CASES)

    assert [paper.pmid for paper in papers] == ["333", "444"]
    assert papers[0].abstract == "English plain-language abstract."
    assert papers[0].journal == "Medline Journal"
    assert papers[0].year is None
    assert papers[1].title == "SPP1 in the tumour microenvironment"
    assert papers[1].journal == "Colorectal Cancer Biology"
    assert papers[1].abstract == "Book chapter abstract."
    assert papers[1].year == 2021


def test_search_pubmed_runs_esearch_then_batched_efetch():
    session = FakeSession(
        FakeResponse(ESEARCH_TWO),
        FakeResponse(EFETCH_TWO),
    )
    clock = FakeClock()
    client = make_client(
        session,
        clock,
        api_key="secret-key",
        email="developer@example.org",
        tool="spatial_omics_test",
    )

    result = search_pubmed("EPCAM AND colorectal cancer", 2, client=client)

    assert result.ok
    assert [paper.pmid for paper in result.papers] == ["111", "222"]
    assert session.calls[0]["url"].endswith("esearch.fcgi")
    assert session.calls[1]["url"].endswith("efetch.fcgi")
    assert all(call["method"] == "POST" for call in session.calls)
    assert session.calls[0]["params"]["api_key"] == "secret-key"
    assert session.calls[0]["params"]["email"] == "developer@example.org"
    assert session.calls[1]["params"]["id"] == "111,222"
    assert clock.sleeps == pytest.approx([0.1])


def test_search_pubmed_without_api_key_uses_three_request_per_second_tier():
    session = FakeSession(
        FakeResponse(ESEARCH_TWO),
        FakeResponse(EFETCH_TWO),
    )
    clock = FakeClock()

    result = search_pubmed(
        "EPCAM",
        2,
        client=make_client(session, clock, api_key=""),
    )

    assert result.ok
    assert "api_key" not in session.calls[0]["params"]
    assert clock.sleeps == pytest.approx([1 / 3])


def test_search_pubmed_zero_results_is_safe_and_skips_efetch():
    session = FakeSession(FakeResponse(ESEARCH_EMPTY))
    clock = FakeClock()

    result = search_pubmed(
        "no matching term",
        client=make_client(session, clock),
    )

    assert result.papers == []
    assert "No matching" in result.status_message
    assert len(session.calls) == 1


def test_rate_limit_response_retries_with_bounded_retry_after():
    session = FakeSession(
        FakeResponse(status_code=429, headers={"Retry-After": "0.25"}),
        FakeResponse(ESEARCH_TWO),
        FakeResponse(EFETCH_TWO),
    )
    clock = FakeClock()
    client = make_client(session, clock, max_retries=1)

    result = search_pubmed("EPCAM", 2, client=client)

    assert result.ok
    assert len(session.calls) == 3
    assert any(delay == pytest.approx(0.25) for delay in clock.sleeps)
    assert all(delay <= 10.0 for delay in clock.sleeps)


def test_retry_after_beyond_budget_fails_without_retrying_early():
    session = FakeSession(
        FakeResponse(status_code=429, headers={"Retry-After": "999"}),
    )
    result = search_pubmed(
        "EPCAM",
        client=make_client(session, FakeClock(), max_retries=2),
    )

    assert result.papers == []
    assert "unavailable" in result.status_message
    assert len(session.calls) == 1


def test_http_200_json_rate_limit_payload_is_retried():
    session = FakeSession(
        FakeResponse(b'{"error": "API rate limit exceeded", "count": "4"}'),
        FakeResponse(ESEARCH_TWO),
        FakeResponse(EFETCH_TWO),
    )
    result = search_pubmed(
        "EPCAM",
        2,
        client=make_client(session, FakeClock(), max_retries=1),
    )

    assert result.ok
    assert len(session.calls) == 3


def test_timeout_and_malformed_xml_return_safe_result():
    timeout_session = FakeSession(
        requests.Timeout("slow"),
        requests.Timeout("still slow"),
    )
    timeout_clock = FakeClock()
    timeout_result = search_pubmed(
        "EPCAM",
        client=make_client(timeout_session, timeout_clock, max_retries=1),
    )

    malformed_session = FakeSession(FakeResponse(b"<broken"))
    malformed_result = search_pubmed(
        "EPCAM",
        client=make_client(malformed_session, FakeClock()),
    )

    assert timeout_result.papers == []
    assert "unavailable" in timeout_result.status_message
    assert malformed_result.papers == []
    assert "unavailable" in malformed_result.status_message


def test_legacy_adapter_returns_fewer_real_results_without_padding():
    class Client:
        def search_ids(self, query: str, max_results: int) -> list[str]:
            assert "EPCAM" in query
            return ["111"]

        def fetch_articles(self, pmids: list[str]) -> list[PubMedPaper]:
            return [
                PubMedPaper(
                    pmid="111",
                    title="A real paper",
                    abstract="First finding. Second finding.",
                    journal="A Journal",
                    year=2024,
                )
            ]

    abstracts = retrieve_abstracts(["EPCAM"], ["Wnt signaling"], n=3, client=Client())

    assert len(abstracts) == 1
    assert set(abstracts[0]) == {"pmid", "title", "journal", "year", "snippet"}
    assert abstracts[0]["pmid"] == "111"


class FakeCollection:
    def __init__(self) -> None:
        self.upsert_call: dict | None = None
        self.query_call: dict | None = None

    def upsert(self, **kwargs) -> None:
        self.upsert_call = kwargs

    def query(self, **kwargs) -> dict:
        self.query_call = kwargs
        assert self.upsert_call is not None
        metadata = self.upsert_call["metadatas"]
        return {
            "ids": [[self.upsert_call["ids"][1], self.upsert_call["ids"][0]]],
            "metadatas": [[metadata[1], metadata[0]]],
            "documents": [[
                self.upsert_call["documents"][1],
                self.upsert_call["documents"][0],
            ]],
            "distances": [[0.2, 0.5]],
        }


def test_semantic_search_indexes_current_corpus_and_returns_similarity_scores():
    result = PubMedResult(
        papers=[
            PubMedPaper(
                "111",
                "EPCAM state",
                "Epithelial tumour-cell state.",
                "Journal A",
                2024,
            ),
            PubMedPaper(
                "222",
                "Wnt state",
                "Wnt signalling in colorectal cancer.",
                "Journal B",
                2023,
            ),
        ]
    )
    collection = FakeCollection()

    matches = semantic_search_abstracts(
        result,
        "Which paper discusses Wnt?",
        top_k=2,
        client=collection,
    )

    assert [match["pmid"] for match in matches] == ["222", "111"]
    assert matches[0]["similarity_score"] == pytest.approx(0.8)
    assert matches[1]["similarity_score"] == pytest.approx(0.5)
    assert all(match["distance_metric"] == "cosine" for match in matches)
    assert collection.query_call["where"]["corpus_id"]
    assert all(
        metadata["corpus_id"] == collection.query_call["where"]["corpus_id"]
        for metadata in collection.upsert_call["metadatas"]
    )


def test_semantic_search_filters_stale_hits_and_handles_empty_input():
    class StaleCollection(FakeCollection):
        def query(self, **kwargs) -> dict:
            return {
                "ids": [["not-in-current-corpus"]],
                "metadatas": [[{"pmid": "999"}]],
                "documents": [["stale"]],
                "distances": [[0.01]],
            }

    result = PubMedResult(
        papers=[PubMedPaper("111", "Current", "Current abstract", "J", 2024)]
    )

    assert semantic_search_abstracts(result, "question", client=StaleCollection()) == []
    assert semantic_search_abstracts(PubMedResult(), "question", client=object()) == []


def test_chroma_integration_isolates_overlapping_pubmed_corpora():
    chromadb = pytest.importorskip("chromadb")
    types = pytest.importorskip("chromadb.api.types")

    class KeywordEmbedding(types.EmbeddingFunction[types.Documents]):
        def __init__(self) -> None:
            self.words = ("wnt", "spp1", "immune")

        def __call__(self, input: types.Documents) -> types.Embeddings:
            return [
                [1.0, *[float(text.lower().count(word)) for word in self.words]]
                for text in input
            ]

        @staticmethod
        def name() -> str:
            return "pubmed-test-keywords"

        def get_config(self) -> dict:
            return {}

        @staticmethod
        def build_from_config(config: dict) -> "KeywordEmbedding":
            return KeywordEmbedding()

    client = chromadb.EphemeralClient()
    collection = client.get_or_create_collection(
        name="pubmed-test-corpus-isolation",
        configuration={"hnsw": {"space": "cosine"}},
        embedding_function=KeywordEmbedding(),
    )
    first = PubMedResult(
        papers=[
            PubMedPaper("111", "Wnt paper", "Wnt colorectal biology", "J", 2024),
            PubMedPaper("222", "Immune paper", "Immune niche", "J", None),
        ]
    )
    second = PubMedResult(
        papers=[
            PubMedPaper("111", "Wnt paper", "Wnt colorectal biology", "J", 2024),
            PubMedPaper("333", "SPP1 paper", "SPP1 macrophages", "J", 2022),
        ]
    )

    first_matches = semantic_search_abstracts(
        first,
        "Wnt",
        top_k=2,
        client=collection,
    )
    second_matches = semantic_search_abstracts(
        second,
        "SPP1",
        top_k=2,
        client=collection,
    )
    stored = collection.get(include=["metadatas"])

    assert first_matches[0]["pmid"] == "111"
    assert second_matches[0]["pmid"] == "333"
    assert all(match["distance_metric"] == "cosine" for match in first_matches)
    assert len(stored["ids"]) == 4
    assert len({metadata["corpus_id"] for metadata in stored["metadatas"]}) == 2
