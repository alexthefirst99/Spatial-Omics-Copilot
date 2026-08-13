from app.image_utils import get_viewer_image_layers, resolve_active_image_path


def test_registered_viewer_layers_are_resolved_by_frontend_index(tmp_path):
    base = tmp_path / "base.tiff"
    overlay = tmp_path / "clusters.png"
    args = {"imageLayers": [str(base), str(overlay)]}

    path, index, layers = resolve_active_image_path(str(tmp_path), args, 1)

    assert path == str(overlay)
    assert index == 1
    assert layers == [str(base), str(overlay)]


def test_old_workspace_discovers_spatial_cluster_layer(tmp_path):
    base_dir = tmp_path / "db" / "data"
    overlay_dir = tmp_path / "user" / "spatial_omics"
    base_dir.mkdir(parents=True)
    overlay_dir.mkdir(parents=True)
    overlay = overlay_dir / "spatial_cluster_overlay.png"
    overlay.touch()
    args = {"sampleId": "sample", "sampleIdFile": "sample-file"}

    layers = get_viewer_image_layers(str(tmp_path), args)

    assert layers == [str(base_dir / "sample-wsi-img.tiff"), str(overlay)]


def test_missing_layer_index_falls_back_to_original(tmp_path):
    base = tmp_path / "base.tiff"
    args = {"imageLayers": [str(base)]}

    path, index, _ = resolve_active_image_path(str(tmp_path), args, 4)

    assert path == str(base)
    assert index == 0
