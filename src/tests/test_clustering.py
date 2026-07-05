from __future__ import annotations

import pytest


pytest.importorskip("scanpy")
pytest.importorskip("rasterio")

from rag.clustering import _cluster_palette


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
