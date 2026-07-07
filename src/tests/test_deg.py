from __future__ import annotations

import pytest


pytest.importorskip("anndata")
pytest.importorskip("shapely")
pytest.importorskip("rasterio")
pytest.importorskip("scanpy")

import anndata as ad
import numpy as np

from rag.deg.extraction import _rank_high_expression_genes


def test_rank_high_expression_genes_orders_positive_fold_change_genes():
    adata = ad.AnnData(
        X=np.array(
            [
                [10.0, 1.0, 0.0],
                [8.0, 1.0, 0.0],
                [1.0, 9.0, 2.0],
                [1.0, 8.0, 2.0],
            ]
        )
    )
    adata.var_names = ["GENE_A", "GENE_B", "GENE_C"]
    selected = np.array([True, True, False, False])

    result = _rank_high_expression_genes(
        adata,
        selected,
        top_n=2,
        ranking_label="roi_vs_non_roi_log2fc",
    )

    assert result["selected_spots"] == 2
    assert result["reference_spots"] == 2
    assert result["ranking_method"] == "roi_vs_non_roi_log2fc"
    assert [gene["gene"] for gene in result["top_genes"]] == ["GENE_A"]
    assert result["top_genes"][0]["log2_fold_change"] > 0


def test_rank_high_expression_genes_empty_selection_returns_no_genes():
    adata = ad.AnnData(X=np.ones((3, 2)))
    adata.var_names = ["GENE_A", "GENE_B"]

    result = _rank_high_expression_genes(
        adata,
        np.array([False, False, False]),
        top_n=5,
        ranking_label="roi_vs_non_roi_log2fc",
    )

    assert result["selected_spots"] == 0
    assert result["reference_spots"] == 3
    assert result["top_genes"] == []
