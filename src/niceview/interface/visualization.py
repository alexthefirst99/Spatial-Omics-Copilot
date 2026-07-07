import os
import numpy as np
import niceview.utils.io as vio
from types import SimpleNamespace
from PIL import Image, ImageDraw
from dash import html
from niceview.pyplot.leaflet import create_viv_viewer
from niceview.interface.data_io import (
    get_data_path_cache_path,
    get_parameter,
    get_user_token_info,
    cache_generate,
)


def get_wsi(folder_id, work_dir, local_img_path=None):
    """
    Get client for wsi image and perform caching.

    Parameters:
        folder_id: unique folder id for each sample

    Returns:
        None
    """
    data_path, cache_path = get_data_path_cache_path(work_dir)
    thor, args, p_input_json = get_parameter(folder_id, work_dir)
    sample_id = args['sampleId']
    sample_id_file = args['sampleId'] + '-' + args['fileName']

    args["sampleIdFile"] = sample_id_file

    args["sampleIdFile"] = sample_id_file

    vio.dump_json(args, f'{work_dir}/user{folder_id}/args.json')
    thor.wsi_gis(sample_id, local_img_path=local_img_path)
    cache = cache_generate(sample_id, sample_id_file=sample_id_file)
    vio.copy(vio.join_path(cache_path, cache["gis-img"]), vio.join_path(cache_path, cache["gis-img-file"]))


def get_spatial_spot_overlay(work_dir, folder_id=""):
    """Return spatial spot coordinates for viewer overlay."""
    state_path = f'{work_dir}/user{folder_id}/spatial_omics.json'
    if not vio.exists(state_path):
        return []

    try:
        import anndata as ad

        state = vio.load_json(state_path)
        h5ad_path = state.get("h5ad_path")
        if not h5ad_path or not vio.exists(h5ad_path):
            return []

        adata = ad.read_h5ad(h5ad_path, backed="r")
        if "spatial" not in adata.obsm:
            if getattr(adata, "file", None) is not None:
                adata.file.close()
            return []

        spatial = np.asarray(adata.obsm["spatial"])
        radius = None
        radius_source = "fallback"
        diameter_keys = (
            "spot_diameter_fullres",
            "spot_diameter",
            "bin_diameter_fullres",
            "bin_size_fullres",
            "fiducial_diameter_fullres",
        )

        def read_diameter_from_scalefactors(scalefactors, source):
            if not isinstance(scalefactors, dict):
                return None, None
            for key in diameter_keys:
                value = scalefactors.get(key)
                if value is not None:
                    try:
                        return float(value), f"{source}.scalefactors.{key}"
                    except (TypeError, ValueError):
                        pass
            return None, None

        spatial_uns = adata.uns.get("spatial", {}) if hasattr(adata, "uns") else {}
        if isinstance(spatial_uns, dict):
            diameter, source = read_diameter_from_scalefactors(
                spatial_uns.get("scalefactors"),
                'adata.uns["spatial"]'
            )
            if diameter:
                radius = diameter / 2.0
                radius_source = source
            else:
                for library_id, library in spatial_uns.items():
                    if not isinstance(library, dict):
                        continue
                    diameter, source = read_diameter_from_scalefactors(
                        library.get("scalefactors"),
                        f'adata.uns["spatial"]["{library_id}"]'
                    )
                    if diameter:
                        radius = diameter / 2.0
                        radius_source = source
                        break

        if radius is None:
            x_span = float(np.ptp(spatial[:, 0])) if spatial.shape[0] else 0
            y_span = float(np.ptp(spatial[:, 1])) if spatial.shape[0] else 0
            radius = max(2.0, min(28.0, min(x_span, y_span) / 180.0 if min(x_span, y_span) else 6.0))

        cluster_path = state.get("cluster_path")
        clusters = {}
        palette = {}
        cluster_key = state.get("cluster_key", "spatial_cluster")
        if cluster_path and vio.exists(cluster_path):
            try:
                cluster_state = vio.load_json(cluster_path)
                clusters = cluster_state.get("clusters", {}) or {}
                palette = cluster_state.get("palette", {}) or {}
                cluster_key = cluster_state.get("cluster_key", cluster_key)
            except Exception as e:
                print(f"Failed to read spatial cluster overlay: {e}")

        obs_names = list(map(str, adata.obs_names))
        spots = []
        for i in range(spatial.shape[0]):
            spot_id = obs_names[i] if i < len(obs_names) else str(i)
            cluster = clusters.get(spot_id)
            spot = {
                "id": spot_id,
                "x": float(spatial[i, 0]),
                "y": float(spatial[i, 1]),
                "r": float(radius),
            }
            if cluster is not None:
                cluster = str(cluster)
                spot["cluster"] = cluster
                spot["cluster_key"] = cluster_key
                spot["color"] = palette.get(cluster)
            spots.append(spot)

        if getattr(adata, "file", None) is not None:
            adata.file.close()
        print(f"Prepared {len(spots)} spatial spots for viewer overlay; spot_radius={radius:.3f} from {radius_source}.")
        return spots
    except Exception as e:
        print(f"Failed to prepare spatial spot overlay: {e}")
        return []


def get_spatial_cluster_overlay_client(work_dir, folder_id, spots, image_size):
    """Create a transparent PNG overlay so clusters load as a normal Viv image layer."""
    if not spots:
        return None
    clustered_spots = [spot for spot in spots if isinstance(spot, dict) and spot.get("cluster") is not None]
    if not clustered_spots:
        return None

    try:
        height, width = image_size
        height = int(height)
        width = int(width)
    except Exception:
        return None
    if height <= 0 or width <= 0:
        return None
    if height * width > 150_000_000:
        print(
            "Spatial cluster image layer skipped: "
            f"{width}x{height} would be too large for a full raster layer."
        )
        return None

    overlay_dir = f'{work_dir}/user{folder_id}/spatial_omics'
    vio.ensure_dir(overlay_dir)
    overlay_path = vio.join_path(overlay_dir, "spatial_cluster_overlay.png")
    state_path = f'{work_dir}/user{folder_id}/spatial_omics.json'
    cluster_mtime = 0
    if vio.exists(state_path):
        state = vio.load_json(state_path)
        cluster_path = state.get("cluster_path")
        if cluster_path and vio.exists(cluster_path):
            cluster_mtime = os.path.getmtime(cluster_path)

    if vio.exists(overlay_path) and os.path.getmtime(overlay_path) >= cluster_mtime:
        return SimpleNamespace(filename=overlay_path)

    def rgba_from_hex(value, alpha=165):
        if not isinstance(value, str) or not value.startswith("#"):
            value = "#0071e3"
        value = value.lstrip("#")
        if len(value) == 3:
            value = "".join(ch * 2 for ch in value)
        try:
            return tuple(int(value[i:i + 2], 16) for i in (0, 2, 4)) + (alpha,)
        except Exception:
            return (0, 113, 227, alpha)

    image = Image.new("RGBA", (width, height), (0, 0, 0, 0))
    draw = ImageDraw.Draw(image, "RGBA")
    drawn = 0
    for spot in clustered_spots:
        try:
            x = float(spot["x"])
            y = float(spot["y"])
            r = max(1.0, float(spot.get("r", 4)))
        except (TypeError, ValueError, KeyError):
            continue
        color = rgba_from_hex(spot.get("color"), alpha=255)
        draw.ellipse((x - r, y - r, x + r, y + r), fill=color)
        drawn += 1

    image.save(overlay_path)
    print(f"Prepared spatial cluster PNG layer: {overlay_path} ({drawn} spots).")
    return SimpleNamespace(filename=overlay_path)


def visualization_img_input(folder_id, work_dir, geojson_coords=None):
    """
    Visualize input image.

    Parameters:
        data_path (str): The path to the data directory.
        cache_path (str): The path to the cache directory.

    Returns:
        obj: The input map object.
    """
    data_path, cache_path = get_data_path_cache_path(work_dir)
    thor, args, p_input_json = get_parameter(folder_id, work_dir)
    sample_id_file = args['sampleIdFile']

    user_token_info = get_user_token_info(work_dir, folder_id)
    server_port = user_token_info["tile-port"]
    workspace_id = user_token_info.get("workspace") or user_token_info["user-token"]

    wsi_client, wsi_layer = thor.gis_client_and_layer(sample_id_file, 'gis-wsi-img', server_port=server_port)

    if wsi_client is None:
        return html.Div([
            html.H4("Error: File not found", style={'color': 'red'}),
            html.P("The requested file could not be found on S3 after waiting 20 seconds."),
            html.P("Please ensure the file has been uploaded.")
        ], style={'padding': '20px', 'border': '1px solid red', 'borderRadius': '5px'})

    spot_overlay = get_spatial_spot_overlay(work_dir, folder_id)
    cluster_overlay_client = get_spatial_cluster_overlay_client(
        work_dir,
        folder_id,
        spot_overlay,
        args.get("heightWidth", [0, 0])
    )
    overlay_layers = [(cluster_overlay_client, "spatial clusters")] if cluster_overlay_client else []

    input_map = create_viv_viewer(
        'map-output',
        wsi_client,
        wsi_layer,
        overlay_layers,
        cmax=0,
        geojson_coords=geojson_coords,
        workspace_id=workspace_id,
        spots=spot_overlay,
    )
    return input_map
