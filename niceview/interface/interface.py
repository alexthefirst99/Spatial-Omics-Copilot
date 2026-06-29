from niceview.utils.dataset import ThorQuery
from niceview.pyplot.leaflet import create_viv_viewer
# from interface.biogis_inter.leaflet import *
from dash import html
import os
import numpy as np
import niceview.utils.io as vio
from types import SimpleNamespace
from PIL import Image, ImageDraw

def add_token_mapping(work_dir, folder_id, user_token_info):
    """
    add token mapping to args.json

    Args:
        work_dir: working directory
        folder_id: unique folder id for each sample
        user_token_info: list, user_token_info[0] is the dash app port, 
                         user_token_info[1] is the token for the user,
                         user_token_info[2] is the tile_server_port for the admin

    Returns:
        None
    """
    args = vio.load_json(f'{work_dir}/user{folder_id}/args.json')
    args['user-token'] = user_token_info
    vio.dump_json(args, f'{work_dir}/user{folder_id}/args.json')

def get_user_token_info(work_dir, folder_id):
    """
    get user token info from args.json

    Args:
        work_dir: working directory
        folder_id: unique folder id for each sample

    Returns:
        user_token_info: dict 
                            user_token_info = {
                                    "user-port": args.port,
                                    "user-token": args.token,
                                    "tile-port": int(args.port)+1,
                        }
    """
    args = vio.load_json(f'{work_dir}/user{folder_id}/args.json')
    user_token_info = args['user-token']
    return user_token_info


def get_data_path_cache_path(work_dir):
    configs = vio.load_toml(f'{work_dir}/user/config.toml')
    data_path = configs['path']['data']
    cache_path = configs['path']['cache']
    return data_path, cache_path


def prepare_file_folder(folder_id, work_dir):
    vio.ensure_dir(f'{work_dir}/user{folder_id}')
    vio.ensure_dir(f'{work_dir}/db')
    vio.ensure_dir(f'{work_dir}/db/data')
    vio.ensure_dir(f'{work_dir}/db/cache')

    args = {
        "sampleId": "spatial-omics-sample",
        "folderId": folder_id,
        "fileName": "file-name",
        "sampleIdFile": "spatial-omics-sample-file-name",
        "heightWidth": [9626, 9843],
    }
    vio.dump_json(args, f'{work_dir}/user{folder_id}/args-default.json')

    vio.dump_json({}, f'{work_dir}/user{folder_id}/previous-input-default.json')

    vio.dump_toml({
        "path": {
            "data": "",
            "cache": "",
        },
        "constant": {
            "cmin": 0,
            "cmax": 255,
            "max_file_size": 5000,
        },
    }, f'{work_dir}/user{folder_id}/config.toml')

    vio.dump_json({
        "data_extension": {
            "wsi-img": "tiff",
        },
        "cache_extension": {
            "gis-wsi-img": "tiff",
        },
        "cell_label_encoder": {},
        "cell_label_cmap": {},
        "primary_key_list": [],
    }, f'{work_dir}/db/db-info.json', indent=4)

def update_data_cache(folder_id, work_dir):
    """
    Get the realpath of data and cache folder

    Parameters:
        None

    Returns:
        None
    """
    configs = vio.load_toml(f'{work_dir}/user{folder_id}/config.toml')
    configs['path']['data'] = f'{work_dir}/db/data/'
    configs['path']['cache'] = f'{work_dir}/db/cache/'
    vio.dump_toml(configs, f'{work_dir}/user{folder_id}/config.toml')


def dump_default_para_arg(folder_id, work_dir):
    """
    Sets the application parameters to their default values.

    Parameters:
        None

    Returns:
        None
    """
    args_default = vio.load_json(f'{work_dir}/user{folder_id}/args-default.json')
    vio.dump_json(args_default, f'{work_dir}/user{folder_id}/args.json')
    p_input_default = vio.load_json(f'{work_dir}/user{folder_id}/previous-input-default.json')
    vio.dump_json(p_input_default, f'{work_dir}/user{folder_id}/previous-input.json')


def dumpjson_parameter_from_user_input(folder_id, work_dir, args=None, p_input_json=None):
    """
    Dump user-provided parameters and input JSON to corresponding files.

    Parameters:
        folder_id (str): Unique folder identifier for each sample.

        args (dict, optional): A dictionary containing user-provided parameters. Default is None.

        p_input_json (dict, optional): A dictionary containing previous input JSON data.
            Default is None.

    Returns:
        None
    """
    if args is not None:
        vio.dump_json(args, f'{work_dir}/user{folder_id}/args.json')
    if p_input_json is not None:
        vio.dump_json(p_input_json, f'{work_dir}/user{folder_id}/previous-input.json')


def files_generate(sample_id):
    return {
        'img': '-'.join([sample_id, 'wsi-img.tiff']),
    }


def cache_generate(sample_id, sample_id_file=''):
    return {
        'gis-img': '-'.join([sample_id, 'gis-wsi-img.tiff']),
        'gis-img-file': '-'.join([sample_id_file, 'gis-wsi-img.tiff']),
    }


def get_parameter(folder_id, work_dir):
    """
    Get parameters from configuration files and create a ThorQuery object.

    Parameters:
        folder_id: unique folder id for each sample

    Returns:
        tuple: A tuple containing ThorQuery object and parameters.
    """
    data_path, cache_path = get_data_path_cache_path(work_dir)
    db_info = vio.load_json(f'{work_dir}/db/db-info.json')
    data_extension = db_info['data_extension']
    cache_extension = db_info['cache_extension']
    cell_label_encoder = db_info['cell_label_encoder']
    cell_label_cmap = db_info['cell_label_cmap']
    primary_key_list = db_info['primary_key_list']
    args = vio.load_json(f'{work_dir}/user{folder_id}/args.json')
    p_input_json = vio.load_json(f'{work_dir}/user{folder_id}/previous-input.json')
    thor = ThorQuery(
        data_path,
        cache_path,
        data_extension,
        cache_extension,
        cell_label_encoder,
        cell_label_cmap,
        primary_key_list,
    )
    
    return thor, args, p_input_json


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
    token = user_token_info["user-token"]

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
        token=token,
        spots=spot_overlay,
    )
    return input_map
