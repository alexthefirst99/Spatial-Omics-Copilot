from __future__ import annotations

import anndata as ad
import numpy as np
import pytest


pytest.importorskip("scanpy")
pytest.importorskip("rasterio")

from rag.clustering import _cluster_palette, run_spatial_clustering


def test_cluster_palette_is_stable_and_numeric_labels_sort_numerically():
    palette = _cluster_palette(["10", "2", "1", "2"])

    assert list(palette) == ["1", "2", "10"]
    assert palette["1"] == "#0071e3"
    assert palette["2"] == "#ff3b30"
    assert palette["10"] == "#34c759"


def test_cluster_palette_handles_mixed_numeric_and_text_labels():
    palette = _cluster_palette(["tumor", "2", "stroma", "1"])

    assert list(palette) == ["1", "2", "stroma", "tumor"]
    assert all(color.startswith("#") and len(color) == 7 for color in palette.values())


def test_run_spatial_clustering_reuses_existing_obs_labels(tmp_path):
    h5ad_path = tmp_path / "existing_clusters.h5ad"
    cluster_path = tmp_path / "clusters.json"
    adata = ad.AnnData(np.ones((4, 3)))
    adata.obsm["spatial"] = np.array([[0, 0], [1, 0], [0, 1], [1, 1]])
    adata.obs["leiden"] = ["0", "0", "1", "1"]
    adata.write_h5ad(h5ad_path)

    payload = run_spatial_clustering(str(h5ad_path), str(cluster_path))

    assert payload["method"] == "existing_obs_leiden"
    assert payload["n_clusters"] == 2
    assert payload["clusters"] == {
        "0": "0",
        "1": "0",
        "2": "1",
        "3": "1",
    }


def test_run_spatial_clustering_uses_current_cache(tmp_path):
    h5ad_path = tmp_path / "cached.h5ad"
    cluster_path = tmp_path / "clusters.json"
    adata = ad.AnnData(np.ones((4, 3)))
    adata.obsm["spatial"] = np.array([[0, 0], [1, 0], [0, 1], [1, 1]])
    adata.obs["leiden"] = ["0", "0", "1", "1"]
    adata.write_h5ad(h5ad_path)

    first_payload = run_spatial_clustering(str(h5ad_path), str(cluster_path))
    cached_payload = run_spatial_clustering(str(h5ad_path), str(cluster_path))

    assert cached_payload == first_payload
