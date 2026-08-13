import os
import json
import threading
import numpy as np
import tifffile
from PIL import Image as _PILImage
_PILImage.MAX_IMAGE_PIXELS = None

from app.config import get_path

_PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), '..'))
TMP_BASE = get_path('paths.tmp_base', os.path.join(_PROJECT_ROOT, 'tmp_data'), env='COPILOT_TMP_BASE')
os.makedirs(TMP_BASE, exist_ok=True)

OME_CACHE_LOCKS = {}
OME_CACHE_LOCKS_GUARD = threading.Lock()
OME_CACHE_FORMAT_VERSION = "v2-rgb-sizec"


def get_viewer_image_layers(work_dir, args, folder_id=""):
    """Return the image paths in the same zero-based order as VivViewer."""
    sample_id = args.get("sampleId", "default")
    base_path = args.get("tutorialImagePath") or os.path.join(
        work_dir, "db", "data", f"{sample_id}-wsi-img.tiff"
    )

    registered_layers = args.get("imageLayers")
    if isinstance(registered_layers, list) and registered_layers:
        layers = [path for path in registered_layers if isinstance(path, str) and path]
        if layers:
            return layers

    # Compatibility for workspaces created before imageLayers was persisted.
    layers = [base_path]
    sample_id_file = args.get("sampleIdFile")
    legacy_overlay = (
        os.path.join(
            work_dir,
            "db",
            "cache",
            f"{sample_id_file}-gis-blend-cell-type-img.tiff",
        )
        if sample_id_file
        else None
    )
    spatial_overlay = os.path.join(
        work_dir, f"user{folder_id}", "spatial_omics", "spatial_cluster_overlay.png"
    )
    for overlay_path in (legacy_overlay, spatial_overlay):
        if overlay_path and os.path.exists(overlay_path):
            layers.append(overlay_path)
    return layers


def resolve_active_image_path(work_dir, args, active_layer=None, folder_id=""):
    """Resolve a frontend layer index to its real backing image path."""
    layers = get_viewer_image_layers(work_dir, args, folder_id=folder_id)
    try:
        layer_index = int(active_layer)
    except (TypeError, ValueError):
        layer_index = 0
    if layer_index < 0 or layer_index >= len(layers):
        layer_index = 0
    return layers[layer_index], layer_index, layers


def crop_image_by_roi(image_path, roi_path, output_path):
    try:
        with open(roi_path, 'r') as f:
            roi_data = json.load(f)

        all_points = []
        if 'features' in roi_data:
            for feature in roi_data['features']:
                geom = feature.get('geometry', {})
                coords = geom.get('coordinates', [])
                if geom.get('type') == 'Polygon':
                    for ring in coords:
                        all_points.extend(ring)
                elif geom.get('type') == 'MultiPolygon':
                    for poly in coords:
                        for ring in poly:
                            all_points.extend(ring)
        elif 'geometry' in roi_data:
            geom = roi_data['geometry']
            coords = geom.get('coordinates', [])
            if geom.get('type') == 'Polygon':
                for ring in coords:
                    all_points.extend(ring)
        if not all_points and isinstance(roi_data, list):
            for poly in roi_data:
                all_points.extend(poly)

        if not all_points:
            print("No coordinates found in ROI file.")
            return False

        pts = np.array(all_points)
        x_min, y_min = np.min(pts, axis=0)
        x_max, y_max = np.max(pts, axis=0)
        x_min = max(0, int(x_min))
        y_min = max(0, int(y_min))
        x_max = int(x_max)
        y_max = int(y_max)

        crop = None
        try:
            import rasterio
            from rasterio.windows import Window
            with rasterio.open(image_path) as src:
                x_min = max(0, min(x_min, src.width))
                y_min = max(0, min(y_min, src.height))
                x_max = max(0, min(x_max, src.width))
                y_max = max(0, min(y_max, src.height))
                if x_max > x_min and y_max > y_min:
                    window = Window(x_min, y_min, x_max - x_min, y_max - y_min)
                    crop = src.read(window=window)
                    crop = np.moveaxis(crop, 0, -1)
                    if crop.shape[2] == 1:
                        crop = crop[:, :, 0]
        except Exception as e_rio:
            print(f"Rasterio crop failed ({e_rio}), trying tifffile.")
            try:
                with tifffile.TiffFile(image_path) as tif:
                    page = tif.pages[0]
                    ih, iw = page.shape[0], page.shape[1]
                    x_max = min(x_max, iw)
                    y_max = min(y_max, ih)
                    if x_max > x_min and y_max > y_min:
                        crop = page.asarray()[y_min:y_max, x_min:x_max]
            except Exception as e_tif:
                print(f"tifffile failed: {e_tif}. Trying PIL.")
                try:
                    img = _PILImage.open(image_path)
                    iw, ih = img.size
                    x_max = min(x_max, iw)
                    y_max = min(y_max, ih)
                    if x_max > x_min and y_max > y_min:
                        crop = np.array(img.crop((x_min, y_min, x_max, y_max)))
                except Exception as e_pil:
                    print(f"PIL failed: {e_pil}")

        if crop is not None and crop.size > 0:
            if len(crop.shape) == 3 and crop.shape[2] == 4:
                if output_path.lower().endswith(('.jpg', '.jpeg')):
                    crop = crop[:, :, :3]
            _PILImage.fromarray(crop).save(output_path)
            return True
        else:
            print("Crop was empty or failed.")
            return False
    except Exception as e:
        print(f"Error cropping image: {e}")
        return False


def get_ome_cache_path(path, token):
    import hashlib
    # Include the converter format version so a metadata fix cannot keep
    # serving an older, browser-incompatible OME-TIFF forever.
    cache_key = f"{OME_CACHE_FORMAT_VERSION}\0{path}"
    path_hash = hashlib.md5(cache_key.encode()).hexdigest()[:12]
    basename = os.path.splitext(os.path.basename(path))[0]
    parent_cache_dir = os.path.join(TMP_BASE, "ome_tiff_cache")
    ome_cache_dir = os.path.join(parent_cache_dir, token)
    return parent_cache_dir, os.path.join(ome_cache_dir, f"{path_hash}_{basename}.ome.tiff")


def ensure_ome_tiff_cached(path, token):
    from dash_viv_viewer.utils import convert_to_ome_tiff

    parent_cache_dir, ome_local_path = get_ome_cache_path(path, token)
    os.makedirs(os.path.dirname(ome_local_path), exist_ok=True)

    with OME_CACHE_LOCKS_GUARD:
        lock = OME_CACHE_LOCKS.setdefault(ome_local_path, threading.Lock())

    with lock:
        if os.path.exists(ome_local_path):
            print(f"[ome_tiff] Serving cached OME-TIFF: {ome_local_path}")
            return ome_local_path

        if os.path.exists(path):
            print(f"[ome_tiff] Converting local file to OME-TIFF: {ome_local_path}")
            convert_to_ome_tiff(path, ome_local_path)
        else:
            raise FileNotFoundError(path)

    return ome_local_path
