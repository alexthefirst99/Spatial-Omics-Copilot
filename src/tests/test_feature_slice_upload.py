from __future__ import annotations

import json

import numpy as np
import pytest

h5py = pytest.importorskip("h5py")
ad = pytest.importorskip("anndata")

from convert_feature_slice_h5 import convert_feature_slice_h5_to_h5ad


def _write_bytes_dataset(group, name, values):
    group.create_dataset(name, data=np.asarray(values, dtype="S256"))


def _write_coo_group(parent, name, rows, cols, values):
    group = parent.create_group(name)
    group.create_dataset("row", data=np.asarray(rows))
    group.create_dataset("col", data=np.asarray(cols))
    group.create_dataset("data", data=np.asarray(values))
    return group


def test_convert_10x_feature_slice_h5_to_h5ad(tmp_path):
    source = tmp_path / "feature_slice.h5"
    stored = tmp_path / "spatial_expression.h5ad"
    with h5py.File(source, "w") as h5_file:
        h5_file.attrs["metadata_json"] = json.dumps({
            "nrows": 16,
            "ncols": 16,
            "spot_pitch": 2.0,
            "transform_matrices": {
                "spot_colrow_to_microscope_colrow": [
                    [2.0, 0.0, 10.0],
                    [0.0, 2.0, 20.0],
                    [0.0, 0.0, 1.0],
                ],
            },
        })
        features = h5_file.create_group("features")
        _write_bytes_dataset(features, "name", ["GeneA", "GeneB", "ADT"])
        _write_bytes_dataset(features, "id", ["gene-a", "gene-b", "adt"])
        _write_bytes_dataset(features, "feature_type", ["Gene Expression", "Gene Expression", "Antibody Capture"])
        _write_bytes_dataset(features, "genome", ["test", "test", "test"])

        feature_slices = h5_file.create_group("feature_slices")
        _write_coo_group(
            feature_slices,
            "0",
            [0, 7, 8, np.nan, 4.5, -1, 16],
            [0, 0, 8, 0, 4, 0, 0],
            [1, 2, 3, 100, 100, 100, 100],
        )
        _write_coo_group(feature_slices, "1", [0, 15], [0, 15], [4, 5])
        _write_coo_group(feature_slices, "2", [0], [0], [9])

        masks = h5_file.create_group("masks")
        _write_coo_group(
            masks,
            "square_016um",
            [0, 1, np.nan, 0.5, -1, 2],
            [0, 1, 0, 0, 0, 0],
            [1, 1, 1, 1, 1, 1],
        )

        umis = h5_file.create_group("umis")
        _write_coo_group(umis, "total", [0, 1], [0, 1], [10, 20])

    info = convert_feature_slice_h5_to_h5ad(str(source), str(stored), binning_scale=8)

    assert info["output_path"] == str(stored)
    assert info["bin_size_um"] == 16
    adata = ad.read_h5ad(stored)
    assert adata.shape == (2, 2)
    assert list(adata.var_names) == ["GeneA", "GeneB"]
    assert adata.X.toarray().tolist() == [[3.0, 4.0], [3.0, 5.0]]
    assert adata.obsm["spatial"].tolist() == [[18.0, 28.0], [34.0, 44.0]]
