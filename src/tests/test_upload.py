from __future__ import annotations

import json
import os

import dash
import numpy as np
import pytest

ad = pytest.importorskip("anndata")

import niceview.interface.upload as upload_module
from niceview.interface.upload import _wait_for_file, upload_spatial_h5ad


def _write_h5ad(path, n_obs=6, n_vars=4, with_spatial=True):
    X = np.arange(n_obs * n_vars, dtype=float).reshape(n_obs, n_vars)
    adata = ad.AnnData(X=X)
    adata.var_names = [f"GENE{i}" for i in range(n_vars)]
    if with_spatial:
        adata.obsm["spatial"] = np.column_stack(
            [np.arange(n_obs, dtype=float), np.arange(n_obs, dtype=float)]
        )
    adata.write_h5ad(path)
    return path


def _no_op_start_clustering(monkeypatch):
    calls = []

    def _fake(stored_path, cluster_path, state_path, job_id=None):
        calls.append((stored_path, cluster_path, state_path, job_id))
        return None

    monkeypatch.setattr(upload_module, "_start_spatial_clustering_background", _fake)
    return calls


def test_upload_spatial_h5ad_valid_file_registers_state_and_starts_clustering(tmp_path, monkeypatch):
    calls = _no_op_start_clustering(monkeypatch)

    source = tmp_path / "sample.h5ad"
    _write_h5ad(source, n_obs=6, n_vars=4, with_spatial=True)
    work_dir = tmp_path / "workdir"

    result = upload_spatial_h5ad([str(source)], "", str(work_dir))

    assert isinstance(result, dash.development.base_component.Component)
    assert "error" not in (result.className or "")

    state_path = upload_module._spatial_omics_state_path(str(work_dir), "")
    with open(state_path) as f:
        state = json.load(f)
    assert state["n_spots"] == 6
    assert state["n_genes"] == 4
    assert state["spatial_key"] == "spatial"

    stored_path = state["h5ad_path"]
    assert ad.read_h5ad(stored_path).n_obs == 6
    assert not source.exists()

    # Clustering is kicked off exactly once, on the stored (not source) copy.
    assert len(calls) == 1
    assert calls[0][0] == stored_path


def test_upload_spatial_h5ad_missing_spatial_key_is_rejected(tmp_path, monkeypatch):
    calls = _no_op_start_clustering(monkeypatch)

    source = tmp_path / "no_spatial.h5ad"
    _write_h5ad(source, n_obs=5, n_vars=3, with_spatial=False)
    work_dir = tmp_path / "workdir"

    result = upload_spatial_h5ad([str(source)], "", str(work_dir))

    assert "error" in result.className
    assert "spatial" in str(result).lower()

    # Nothing should be registered for ROI analysis, and no clustering was started.
    state_path = upload_module._spatial_omics_state_path(str(work_dir), "")
    assert not os.path.exists(state_path)
    assert calls == []

    # The partial copy made before validation failed must not be left behind.
    spatial_dir = upload_module._spatial_omics_dir(str(work_dir), "")
    stored_path = os.path.join(spatial_dir, "spatial_expression.h5ad")
    assert not os.path.exists(stored_path)


def test_upload_spatial_h5ad_rejects_non_h5ad_extension(tmp_path, monkeypatch):
    calls = _no_op_start_clustering(monkeypatch)

    source = tmp_path / "sample.csv"
    source.write_text("not an h5ad file")
    work_dir = tmp_path / "workdir"

    result = upload_spatial_h5ad([str(source)], "", str(work_dir))

    assert "error" in result.className
    assert ".h5ad" in str(result)
    assert calls == []


def test_upload_spatial_h5ad_no_files_returns_no_update():
    assert upload_spatial_h5ad([], "", "/tmp/unused") is dash.no_update
    assert upload_spatial_h5ad(None, "", "/tmp/unused") is dash.no_update


def test_wait_for_file_times_out_instead_of_polling_forever(tmp_path):
    missing = tmp_path / "missing.h5ad"

    with pytest.raises(FileNotFoundError, match="Please select the file and upload it again"):
        _wait_for_file(str(missing), timeout_seconds=0, poll_seconds=0)
