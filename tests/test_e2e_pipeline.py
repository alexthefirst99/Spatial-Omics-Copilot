"""
End-to-end integration pipeline tests (Person 6 / T-029, T-052, T-045).

Covers the real chain in rag.pipeline: map_roi_to_spatial_barcodes(),
prepare_roi_image_for_llm(), and run_integration_pipeline(). Pathway/gene
annotation/PubMed are monkeypatched so the suite has no network dependency;
everything else (preprocessing, clustering, DEG, ROI resolution, image
cropping, agent routing) runs for real against small synthetic data.
"""
from __future__ import annotations

import json
import os

import numpy as np
import pandas as pd
import anndata as ad
import pytest

pytest.importorskip("scanpy")
pytest.importorskip("rasterio")

import niceview.utils.io as vio
from rag.contracts import ROIImageResult, ROISelection
from rag.pipeline import (
    map_roi_to_spatial_barcodes,
    prepare_roi_image_for_llm,
    run_integration_pipeline,
)


def make_synthetic_adata(n_spots=80, n_genes=120, seed=7):
    rng = np.random.default_rng(seed)
    X = rng.poisson(3, size=(n_spots, n_genes)).astype(float)
    # Push up a handful of genes for spots inside the ROI box below, so DEG
    # has something real to find instead of noise.
    inside = (rng.random(n_spots) < 0.5)
    X[inside, :5] += rng.poisson(15, size=(inside.sum(), 5))

    obs = pd.DataFrame(index=[f"spot_{i}" for i in range(n_spots)])
    var = pd.DataFrame(index=[f"gene_{i}" for i in range(n_genes)])
    adata = ad.AnnData(X=X, obs=obs, var=var)
    # Spread spots over a 100x100 grid; spots flagged "inside" sit in the
    # [0, 50) x [0, 50) quadrant so a matching polygon selects exactly them.
    spatial = np.empty((n_spots, 2))
    spatial[inside] = rng.random((inside.sum(), 2)) * 50
    spatial[~inside] = 50 + rng.random(((~inside).sum(), 2)) * 50
    adata.obsm["spatial"] = spatial
    adata.obs["_inside"] = inside
    return adata


def make_synthetic_tiff(path, size=100):
    import tifffile

    rng = np.random.default_rng(0)
    image = rng.integers(0, 255, size=(size, size, 3), dtype=np.uint8)
    tifffile.imwrite(path, image)


def _write_cluster_json(cluster_path, obs_names, cluster_of_obs):
    vio.dump_json(
        {
            "cluster_key": "spatial_cluster",
            "clusters": {name: cluster_of_obs[name] for name in obs_names},
        },
        cluster_path,
    )


@pytest.fixture
def demo_config(tmp_path):
    return {
        "min_genes": 1,
        "min_counts": 1,
        "preprocess_cache_dir": str(tmp_path / "preprocess_cache"),
        "cluster_cache_dir": str(tmp_path / "cluster_cache"),
    }


# --------------------------------------
# map_roi_to_spatial_barcodes (T-052)
# --------------------------------------


def test_map_roi_polygon_selects_only_spots_inside(tmp_path):
    adata = make_synthetic_adata()
    h5ad_path = tmp_path / "test.h5ad"
    adata.write_h5ad(h5ad_path)

    roi = ROISelection(
        selection_type="polygon",
        polygon_points=[[[0, 0], [50, 0], [50, 50], [0, 50]]],
    )
    resolved = map_roi_to_spatial_barcodes(str(h5ad_path), roi, {})

    expected = set(adata.obs_names[adata.obs["_inside"]])
    assert resolved.status_message == ""
    assert set(resolved.barcode_ids) == expected
    assert resolved.spot_ids == resolved.barcode_ids


def test_map_roi_cluster_selects_matching_cluster(tmp_path):
    adata = make_synthetic_adata()
    h5ad_path = tmp_path / "test.h5ad"
    adata.write_h5ad(h5ad_path)
    cluster_path = tmp_path / "clusters.json"

    cluster_of_obs = {
        name: ("0" if inside else "1")
        for name, inside in zip(adata.obs_names, adata.obs["_inside"])
    }
    _write_cluster_json(cluster_path, adata.obs_names, cluster_of_obs)

    roi = ROISelection(selection_type="cluster", cluster_id="0")
    resolved = map_roi_to_spatial_barcodes(
        str(h5ad_path), roi, {"cluster_path": str(cluster_path)}
    )

    expected = {name for name, label in cluster_of_obs.items() if label == "0"}
    assert resolved.status_message == ""
    assert set(resolved.barcode_ids) == expected


def test_map_roi_missing_h5ad_returns_status_message_without_raising(tmp_path):
    roi = ROISelection(selection_type="polygon", polygon_points=[[[0, 0], [1, 0], [1, 1]]])
    resolved = map_roi_to_spatial_barcodes(str(tmp_path / "missing.h5ad"), roi, {})

    assert resolved.barcode_ids == []
    assert resolved.status_message != ""


def test_map_roi_cluster_selection_without_cluster_path_returns_status_message(tmp_path):
    adata = make_synthetic_adata()
    h5ad_path = tmp_path / "test.h5ad"
    adata.write_h5ad(h5ad_path)

    roi = ROISelection(selection_type="cluster", cluster_id="0")
    resolved = map_roi_to_spatial_barcodes(str(h5ad_path), roi, {})

    assert resolved.barcode_ids == []
    assert "cluster" in resolved.status_message.lower()


# --------------------------------------
# prepare_roi_image_for_llm (T-045)
# --------------------------------------


def test_prepare_roi_image_crops_and_reports_metadata(tmp_path):
    image_path = tmp_path / "slide.tif"
    make_synthetic_tiff(image_path, size=100)

    roi = ROISelection(
        roi_id="roi-1",
        selection_type="polygon",
        polygon_points=[[[10, 10], [40, 10], [40, 40], [10, 40]]],
    )
    result = prepare_roi_image_for_llm(
        str(image_path), roi, {"output_dir": str(tmp_path / "crops")}
    )

    assert result.status_message == ""
    assert os.path.exists(result.crop_path)
    assert result.width == 30
    assert result.height == 30
    assert result.image_format == "png"


def test_prepare_roi_image_without_polygon_returns_status_message(tmp_path):
    image_path = tmp_path / "slide.tif"
    make_synthetic_tiff(image_path, size=100)

    roi = ROISelection(selection_type="cluster", cluster_id="0")
    result = prepare_roi_image_for_llm(str(image_path), roi, {})

    assert result.crop_path == ""
    assert result.status_message != ""


def test_prepare_roi_image_missing_file_returns_status_message(tmp_path):
    roi = ROISelection(selection_type="polygon", polygon_points=[[[0, 0], [1, 0], [1, 1]]])
    result = prepare_roi_image_for_llm(str(tmp_path / "missing.tif"), roi, {})

    assert result.crop_path == ""
    assert result.status_message != ""


# --------------------------------------
# run_integration_pipeline (T-029) — the full chain
# --------------------------------------


def test_run_integration_pipeline_end_to_end_with_mocked_external_apis(tmp_path, monkeypatch):
    adata = make_synthetic_adata()
    h5ad_path = tmp_path / "test.h5ad"
    adata.write_h5ad(h5ad_path)

    image_path = tmp_path / "slide.tif"
    make_synthetic_tiff(image_path, size=100)

    def _fake_gene_annotation(genes, config=None):
        from rag.gene_annotation import GeneAnnotationResult

        return GeneAnnotationResult(genes=[], status_message="mocked")

    def _fake_pathway(genes, config=None):
        from rag.pathway_enrichment import PathwayResult

        return PathwayResult(pathways=[], status_message="mocked")

    def _fake_search_pubmed(query, max_results=5, **kwargs):
        from rag.pubmed_retrieval import PubMedResult

        return PubMedResult(papers=[], status_message="mocked", query=query)

    monkeypatch.setattr("rag.pipeline.run_gene_annotation_retrieval", _fake_gene_annotation)
    monkeypatch.setattr("rag.pipeline.run_pathway_enrichment", _fake_pathway)
    monkeypatch.setattr("rag.pipeline.search_pubmed", _fake_search_pubmed)

    roi = ROISelection(
        roi_id="roi-e2e",
        selection_type="polygon",
        polygon_points=[[[0, 0], [50, 0], [50, 50], [0, 50]]],
    )

    result = run_integration_pipeline(
        h5ad_path=str(h5ad_path),
        image_path=str(image_path),
        roi_selection=roi,
        question="What genes are enriched here?",
        config={
            "min_genes": 1,
            "min_counts": 1,
            "preprocess_cache_dir": str(tmp_path / "preprocess_cache"),
            "cluster_cache_dir": str(tmp_path / "cluster_cache"),
        },
    )

    assert result["preprocess"]["qc_summary"]["n_spots"] > 0
    assert result["cluster"]["cluster_summary"]["n_clusters"] >= 1
    assert result["roi"]["status_message"] == ""
    assert len(result["roi"]["barcode_ids"]) > 0
    assert result["roi_image"]["status_message"] == ""
    assert result["deg"]["top_genes"]
    # DEG found real signal: the five genes boosted in make_synthetic_adata.
    top_gene_names = {gene["gene"] for gene in result["deg"]["top_genes"]}
    assert top_gene_names & {f"gene_{i}" for i in range(5)}
    assert result["gene_objects"]
    assert result["context_str"].startswith("\n\n")
    assert "metadata" in result
    assert result["metadata"]["trace"]


def test_run_integration_pipeline_without_image_path_skips_roi_image(tmp_path, monkeypatch):
    adata = make_synthetic_adata()
    h5ad_path = tmp_path / "test.h5ad"
    adata.write_h5ad(h5ad_path)

    def _empty_pathway(genes, config=None):
        from rag.pathway_enrichment import PathwayResult

        return PathwayResult(pathways=[], status_message="mocked")

    def _empty_annotation(genes, config=None):
        from rag.gene_annotation import GeneAnnotationResult

        return GeneAnnotationResult(genes=[], status_message="mocked")

    def _empty_pubmed(query, max_results=5, **kwargs):
        from rag.pubmed_retrieval import PubMedResult

        return PubMedResult(papers=[], status_message="mocked", query=query)

    monkeypatch.setattr("rag.pipeline.run_gene_annotation_retrieval", _empty_annotation)
    monkeypatch.setattr("rag.pipeline.run_pathway_enrichment", _empty_pathway)
    monkeypatch.setattr("rag.pipeline.search_pubmed", _empty_pubmed)

    roi = ROISelection(
        selection_type="polygon",
        polygon_points=[[[0, 0], [50, 0], [50, 50], [0, 50]]],
    )
    result = run_integration_pipeline(
        h5ad_path=str(h5ad_path),
        image_path=None,
        roi_selection=roi,
        config={
            "min_genes": 1,
            "min_counts": 1,
            "preprocess_cache_dir": str(tmp_path / "preprocess_cache"),
            "cluster_cache_dir": str(tmp_path / "cluster_cache"),
        },
    )

    assert result["roi_image"] is None
