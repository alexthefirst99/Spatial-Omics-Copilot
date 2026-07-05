from __future__ import annotations

from rag.pubmed import retrieve_abstracts


REQUIRED_KEYS = {"pmid", "title", "journal", "year", "snippet"}


def test_retrieve_abstracts_ranks_gene_and_pathway_matches_first():
    abstracts = retrieve_abstracts(
        ["SNAP25", "SYP", "GRIA1"],
        pathways=["GO:0007268 · Chemical synaptic transmission"],
        n=2,
    )

    assert len(abstracts) == 2
    assert REQUIRED_KEYS <= abstracts[0].keys()
    assert abstracts[0]["pmid"] == "39012345"
    assert "synaptic" in abstracts[0]["title"].lower()


def test_retrieve_abstracts_pads_to_requested_count_without_matches():
    abstracts = retrieve_abstracts(["NOT_A_REAL_GENE"], pathways=[], n=4)

    assert len(abstracts) == 4
    assert all(REQUIRED_KEYS <= abstract.keys() for abstract in abstracts)
    assert len({abstract["pmid"] for abstract in abstracts}) == 4
