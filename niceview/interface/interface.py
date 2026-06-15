from niceview.utils.dataset import ThorQuery
from niceview.pyplot.leaflet import create_viv_viewer
from niceview.utils.cell_select import cell_selection_main
# from interface.biogis_inter.leaflet import *
from dash import html
import matplotlib as mpl
import shutil
import json
import toml
import os
import zipfile
import re
import pandas as pd
import cv2
import numpy as np
import niceview.utils.io as vio

all_gene_spot_layer = []
gene_spot = []
all_gene_cell_layer = []
gene_cell = []
all_pathway_layer = []
pathway_cell = []

palette_blyl = [
        '#352A86',
        '#343DAE',
        '#0262E0',
        '#1389D2',
        '#2DB7A3',
        '#A5BE6A',
        '#F8BA43',
        '#F6DA23',
        '#F8FA0D'
        ]

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


def get_cv2_cmap(cmap_name, rgb_order=False):
    """
    Extract colormap color information as a LUT compatible with cv2.applyColormap().
    Default channel order is BGR.

    Args:
        cmap_name: string, name of the colormap.
        rgb_order: boolean, if false or not set, the returned array will be in
                   BGR order (standard OpenCV format). If true, the order
                   will be RGB.

    Returns:
        A numpy array of type uint8 containing the colormap.
    """

    if isinstance(cmap_name, str):
        c_map = mpl.cm.get_cmap(cmap_name, 256)
    elif isinstance(cmap_name, mpl.colors.LinearSegmentedColormap):
        c_map = cmap_name
    else:
        raise ValueError("cmap_name must be a string or a matplotlib colormap.")
    rgba_data = mpl.cm.ScalarMappable(cmap=c_map).to_rgba(
        np.arange(0, 256., 1.0), bytes=True
    )
    rgba_data = rgba_data[:, 0:-1].reshape((256, 1, 3))

    # Convert to BGR (or RGB), uint8, for OpenCV.
    cmap = np.zeros((256, 1, 3), np.uint8)

    if not rgb_order:
        cmap[:, :, :] = rgba_data[:, :, ::-1]
    else:
        cmap[:, :, :] = rgba_data[:, :, :]

    return cmap

cmap_blyl = mpl.colors.LinearSegmentedColormap.from_list('my_cmap', palette_blyl, N=256)
cmap_blyl = get_cv2_cmap(cmap_blyl)


def prepare_file_folder(folder_id, work_dir):
    vio.ensure_dir(f'{work_dir}/user{folder_id}')
    vio.ensure_dir(f'{work_dir}/db')
    vio.ensure_dir(f'{work_dir}/db/data')
    vio.ensure_dir(f'{work_dir}/db/cache')
    args = {
        "sampleId": "gt-iz-p9-rep2",
        "folderId": "",
        "fileName": "file-name",
        "selectedCellGeneName": "ENSG00000065534",
        "selectedSpotGeneName": "ENSG00000065534",
        "selectedPathway": "ADIPOGENESIS",
        "maskOpacity": 0.5,
        "sampleIdFile": "gt-iz-p9-rep2-file-name",
        "sampleIdSpotGene": "gt-iz-p9-rep2-ENSG00000065534",
        "sampleIdCellGene": "gt-iz-p9-rep2-ENSG00000065534",
        "sampleIdPathway": "gt-iz-p9-rep2-ADIPOGENESIS",
        "sampleIdArea": "gt-iz-p9-rep2-area1",
        "sampleIdMin": None,
        "sampleIdMax": None,
        "sampleIdMinMax": None,
        "heightWidth": [9626, 9843],
        "cmap": {
        "CMAP_PATHWAY": cv2.COLORMAP_COOL,
#        "CMAP_CELL_GENE": cv2.COLORMAP_RAINBOW,
        "CMAP_CELL_GENE": cmap_blyl.tolist(),
        },

        # Alex please help with this.
        # CMAP_PATHWAY: "coolwarm",
        # CMAP_CELL_GENE: "rainbow",
        # CMAP_SPOT_GENE: "jet",
    }
    vio.dump_json(args, f'{work_dir}/user{folder_id}/args-default.json')
    p_input_json = {
        "SpotGene": [],
        "CellGene": [],
        "CellType": [],
        "Pathway": [],
        "Area": [],
        
    }
    vio.dump_json(p_input_json, f'{work_dir}/user{folder_id}/previous-input-default.json')

    config_content = {
        "path": {
            "data": "",
            "cache": ""
        },
        "constant": {
            "cmin": 0,
            "cmax": 255,
            "max_file_size": 5000
        }
    }
    vio.dump_toml(config_content, f'{work_dir}/user{folder_id}/config.toml')

    db_info_content = {
        "data_extension": {
            "cell": "h5ad",
            "cell-temp": "h5ad",
            "cell-pathway": "h5ad",
            "cell-pathway-name": "txt",
            "cell-pathway-matrix": "npy",
            "cell-gene": "npz",
            "cell-gene-name": "txt",
            "cell-info": "csv",
            "cell-mask": "npz",
            "cell-barcode": "txt",
            "wsi-img": "tiff",
            "spot": "h5ad",
            "spot-temp": "h5ad",
            "spot-gene": "npz",
            "spot-gene-name": "txt",
            "spot-info": "csv"
        },
        "cache_extension": {
            "blend-cell-pathway-heatmap-img": "png",
            "blend-cell-gene-heatmap-img": "png",
            "blend-cell-random-img": "png",
            "blend-cell-gene-img": "png",
            "blend-cell-type-img": "png",
            "blend-cell-select-img": "png",
            "blend-spot-gene-img": "png",
            "cell-pathway-heatmap-img": "png",
            "cell-gene-heatmap-img": "png",
            "mask-cell-random-img": "png",
            "mask-cell-gene-img": "png",
            "mask-cell-type-img": "png",
            "mask-cell-select-img": "png",
            "mask-cell-match-region": "npy",
            "circle-spot-gene-img": "png",
            "gis-blend-cell-pathway-heatmap-img": "tiff",
            "gis-blend-cell-gene-heatmap-img": "tiff",
            "gis-blend-cell-random-img": "tiff",
            "gis-blend-cell-gene-img": "tiff",
            "gis-blend-cell-type-img": "tiff",
            "gis-blend-cell-select-img": "tiff",
            "gis-blend-spot-gene-img": "tiff",
            "gis-wsi-img": "tiff"
        },
        "cell_label_encoder": {
            "Neoplastic": 1,
            "Immune": 2,
            "Stromal": 3,
            "Epithelial": 4,
            "Fibroblast": 5,
            "Endothelial": 6,
            "Cardiomyocyte": 7,
            "Cardiac Fibroblast": 8,
            "Smooth Muscle": 9,
            "Adipose": 10,
            "Oligodendrocyte": 11,
            "Astrocyte": 12,
            "Neuron": 13,
            "Vascular Smooth Muscle": 14,
            "Alveolar pneumocytes": 15,
            "Chondrocytes": 16,
            "Hepatocyte": 17,
            "Glia": 18,
            "Pericentral hepatocytes": 19,
            "Proliferating keratinocytes": 20,
            "Spinous keratinocytes": 21,
            "Connective": 22,
            "Lamina propria": 23
        },
        "cell_label_cmap": {
            "1": "#009df7",
            "2": "#be6ca7",
            "3": "#047802",
            "4": "#3100f7",
            "5": "#FFFFFF",
            "6": "#1d8942",
            "7": "#253166",
            "8": "#853087",
            "9": "#4B72EE"
        },
        "primary_key_list": [
            "gt-iz-p9-rep2"
        ]
    }
    vio.dump_json(db_info_content, f'{work_dir}/db/db-info.json', indent=4)
    

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
    """
    Generate file names for various data and cache files based on the sample_id.

    Parameters:
        sample_id (str): The sample ID.

    Returns:
        dict: A dictionary containing file names for cells, images, and spots.
    """
    files = {
        'cell-h5ad': '-'.join([sample_id, 'cell.h5ad']),
        'cell-temp-h5ad': '-'.join([sample_id, 'cell-temp.h5ad']),
        'cells-gene-names': '-'.join([sample_id, 'cell-gene-name.txt']),
        'cell-info': '-'.join([sample_id, 'cell-info.csv']),
        'cell-barcode': '-'.join([sample_id, 'cell-barcode.txt']),
        'cell-pathway-name': '-'.join([sample_id, 'cell-pathway-name.txt']),
        'img': '-'.join([sample_id, 'wsi-img.tiff']),
        'spot-h5ad': '-'.join([sample_id, 'spot.h5ad']),
        'spot-temp-h5ad': '-'.join([sample_id, 'spot-temp.h5ad']),
        'spots-gene-names': '-'.join([sample_id, 'spot-gene-name.txt']),
        'mask': '-'.join([sample_id, 'cell-mask.npz']),
        'cell-gene': '-'.join([sample_id, 'cell-gene.npz']),
        

    }
    return files


def cache_generate(sample_id, sample_id_file='', sample_id_gene_cell='', sample_id_gene_spot='', sample_id_pathway='', sample_id_area='',sample_id_gene_min='', sample_id_gene_max='', sample_id_gene_minmax=''):
    """
    Generate cache file names based on sample IDs and file names.

    Parameters:
        sample_id (str): The sample ID.
        sample_id_file (str): The sample ID with the file name.
        sample_id_gene_cell (str): The sample ID with the cell gene name.
        sample_id_gene_spot (str): The sample ID with the spot gene name.

    Returns:
        dict: A dictionary containing cache file names.
    """
    cache = {
        'gis-img': '-'.join([sample_id, 'gis-wsi-img.tiff']),
        'gis-blend-cells': '-'.join([sample_id, 'gis-blend-cell-gene-img.tiff']),
        'gis-blend-spots': '-'.join([sample_id, 'gis-blend-spot-gene-img.tiff']),
        'gis-blend-cell-type': '-'.join([sample_id, 'gis-blend-cell-type-img.tiff']),
        'gis-blend-cell-heatmap': '-'.join([sample_id, 'gis-blend-cell-pathway-heatmap-img.tiff']),
        'gis-blend-cell-random-img': '-'.join([sample_id, 'gis-blend-cell-random-img.tiff']),
        'gis-blend-cell-select-img': '-'.join([sample_id, 'gis-blend-cell-select-img.tiff']),

        'gis-img-file': '-'.join([sample_id_file, 'gis-wsi-img.tiff']),
        'gis-blend-cells-gene': '-'.join([sample_id_gene_cell, 'gis-blend-cell-gene-img.tiff']),
        'gis-blend-cells-gene-min': '-'.join([sample_id_gene_min, 'gis-blend-cell-gene-img.tiff']),
        'gis-blend-cells-gene-max': '-'.join([sample_id_gene_max, 'gis-blend-cell-gene-img.tiff']),
        'gis-blend-cells-gene-minmax': '-'.join([sample_id_gene_minmax, 'gis-blend-cell-gene-img.tiff']),
        'gis-blend-spots-gene': '-'.join([sample_id_gene_spot, 'gis-blend-spot-gene-img.tiff']),
        'gis-blend-spots-gene-min': '-'.join([sample_id_gene_min, 'gis-blend-spot-gene-img.tiff']),
        'gis-blend-spots-gene-max': '-'.join([sample_id_gene_max, 'gis-blend-spot-gene-img.tiff']),
        'gis-blend-spots-gene-minmax': '-'.join([sample_id_gene_minmax, 'gis-blend-spot-gene-img.tiff']),
        'gis-blend-cell-type-file': '-'.join([sample_id_file, 'gis-blend-cell-type-img.tiff']),
        'gis-blend-cell-pathway-heatmap': '-'.join([sample_id_pathway, 'gis-blend-cell-pathway-heatmap-img.tiff']),
        'gis-blend-cell-random-img-file': '-'.join([sample_id_file, 'gis-blend-cell-random-img.tiff']),
        'gis-blend-cell-select-img-area': '-'.join([sample_id_area, 'gis-blend-cell-select-img.tiff']),
    }
    return cache


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


def update_javascript(file_path, new_factor, dest_path=None):
    """
    Update a specific line in a JavaScript file with a new mathematical expression.

    Parameters:
        new_factor (str): The new mathematical expression to replace the existing one.
            This should be a string representing a JavaScript expression. For example,
            'e*2.5' will replace the existing expression with 'Math.round(e*2.5)'.

    Returns:
        None
    """
    # Read the file content
    with vio.open_file(file_path, 'r') as file:
        content = file.read()
    # Define the pattern to match the line you want to replace
    pattern = r'_updateMetric:function\(t\){var e=this\._getRoundNum\(t\),n=Math\.round\(e\*[-+]?\d*\.\d+\)'
    # Define the replacement string
    replacement = r'_updateMetric:function(t){var e=this._getRoundNum(t),n=Math.round(' + new_factor + ')'

    # Perform the replacement
    new_content = re.sub(pattern, replacement, content)

    # Save the modified content back to the file
    if dest_path is None:
        dest_path = file_path
    with vio.open_file(dest_path, 'w') as file:
        file.write(new_content)


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
    

def calculation_cell(folder_id, work_dir, vmin=None, vmax=None):
    """
    Perform cell gene analysis and caching.

    Parameters:
        folder_id: unique folder id for each sample
        label_analysis (bool): Whether to perform cell type analysis.

    Returns:
        None
    """
    data_path, cache_path = get_data_path_cache_path(work_dir)
    thor, args, p_input_json = get_parameter(folder_id, work_dir)
    sample_id = args['sampleId']
    sample_id_file = args['sampleIdFile']
    selected_cell_gene_name = args['selectedCellGeneName']
    sample_id_gene_cell = sample_id + "-" + selected_cell_gene_name
    height = args["heightWidth"][0]
    width = args["heightWidth"][1]
    max_dim = max(height, width)
    cmap = args['cmap']["CMAP_CELL_GENE"]


    # added when cmap is a ndarray
    if isinstance(cmap, list):
        cmap = np.array(cmap, dtype=np.uint8)

    thor.empty_cache_cell(sample_id, gene=True, label=True)
    
    args['sampleIdCellGene'] = sample_id_gene_cell

    args['sampleIdCellGene'] = sample_id_gene_cell

    vio.dump_json(args, f'{work_dir}/user{folder_id}/args.json')

    thor.cell_gis(
        sample_id,
        max_dim,
        selected_cell_gene_name,
        vmin=vmin, 
        vmax=vmax,
        cmap=cmap
    )

    if vmin is not None or vmax is not None:
        sample_id_min = args["sampleIdMin"]
        sample_id_max = args["sampleIdMax"]
        sample_id_minmax = args["sampleIdMinMax"]

        if vmin is not None and vmax is not None:
            cache = cache_generate(sample_id, sample_id_gene_minmax=sample_id_minmax)
            vio.copy(vio.join_path(cache_path, cache["gis-blend-cells"]), vio.join_path(cache_path, cache["gis-blend-cells-gene-minmax"]))
        elif vmax is not None:
            cache = cache_generate(sample_id, sample_id_gene_max=sample_id_max)
            vio.copy(vio.join_path(cache_path, cache["gis-blend-cells"]), vio.join_path(cache_path, cache["gis-blend-cells-gene-max"]))
        elif vmin is not None:
            cache = cache_generate(sample_id, sample_id_gene_min=sample_id_min)
            vio.copy(vio.join_path(cache_path, cache["gis-blend-cells"]), vio.join_path(cache_path, cache["gis-blend-cells-gene-min"]))
    else:
        cache = cache_generate(sample_id, sample_id_gene_cell=sample_id_gene_cell, sample_id_file=sample_id_file)
        vio.copy(vio.join_path(cache_path, cache["gis-blend-cells"]), vio.join_path(cache_path, cache["gis-blend-cells-gene"]))
    # vio.copy(vio.join_path(cache_path, cache["gis-blend-cell-type"]), vio.join_path(cache_path, cache["gis-blend-cell-type-file"]))


def calculation_pathway(folder_id, work_dir):
    """
    Perform pathway analysis and caching.

    Parameters:
        folder_id: unique folder id for each sample
        label_analysis (bool): Whether to perform cell type analysis.

    Returns:
        None
    """
    data_path, cache_path = get_data_path_cache_path(work_dir)
    thor, args, p_input_json = get_parameter(folder_id, work_dir)
    sample_id = args['sampleId']
    selected_pathway = args["selectedPathway"]
    cmap = args['cmap']["CMAP_PATHWAY"]
    height = args["heightWidth"][0]
    width = args["heightWidth"][1]
    max_dim = max(height, width)
    
    sample_id_pathway = sample_id + "-" + selected_pathway

    thor.empty_cache_cell(sample_id, pathway=True)
    
    args['sampleIdPathway'] = sample_id_pathway

    args['sampleIdPathway'] = sample_id_pathway

    vio.dump_json(args, f'{work_dir}/user{folder_id}/args.json')

    thor.cell_gis(
        sample_id,
        max_dim,
        selected_pathway=selected_pathway,
        label_analysis=False,
        cmap = cmap
    )


    cache = cache_generate(sample_id, sample_id_pathway=sample_id_pathway)
    cache = cache_generate(sample_id, sample_id_pathway=sample_id_pathway)
    vio.copy(vio.join_path(cache_path, cache["gis-blend-cell-heatmap"]), vio.join_path(cache_path, cache["gis-blend-cell-pathway-heatmap"]))


def calculation_CNV(folder_id, work_dir ):
    """
    Perform CNV analysis and caching.

    Parameters:
        folder_id: unique folder id for each sample
        label_analysis (bool): Whether to perform cell type analysis.

    Returns:
        None
    """
    data_path, cache_path = get_data_path_cache_path(work_dir)
    thor, args, p_input_json = get_parameter(folder_id, work_dir)
    sample_id = args['sampleId']
    sample_id_file = args['sampleIdFile']
    height = args["heightWidth"][0]
    width = args["heightWidth"][1]
    max_dim = max(height, width)

    p_input_json = vio.load_json(f'{work_dir}/user{folder_id}/previous-input.json')

    if sample_id_file not in p_input_json["CellType"]:
        p_input_json["CellType"].append(sample_id_file)

    vio.dump_json(p_input_json, f'{work_dir}/user{folder_id}/previous-input.json')

    thor.empty_cache_cell(sample_id, label=True)

    thor.cell_gis(
        sample_id,
        max_dim,
        label_analysis=True,
    )
    
    cache = cache_generate(sample_id, sample_id_file=sample_id_file)
    cache = cache_generate(sample_id, sample_id_file=sample_id_file)
    vio.copy(vio.join_path(cache_path, cache["gis-blend-cell-type"]), vio.join_path(cache_path, cache["gis-blend-cell-type-file"]))


def calculation_cell_detection(folder_id, work_dir):
    
    thor, args, p_input_json = get_parameter(folder_id, work_dir)
    data_path, cache_path = get_data_path_cache_path(work_dir)
    sample_id = args['sampleId']
    sample_id_file = args['sampleIdFile']
    height = args["heightWidth"][0]
    width = args["heightWidth"][1]
    max_dim = max(height, width)
    cache = cache_generate(sample_id, sample_id_file=sample_id_file)
    # get name by rule and copy to cache folder
    
    vio.copy(vio.join_path(cache_path, cache["gis-blend-cell-random-img"]), vio.join_path(cache_path, cache["gis-blend-cell-random-img-file"]))
    if False:
        pass

def calculation_similar_cell(folder_id, work_dir):
    thor, args, p_input_json = get_parameter(folder_id, work_dir)
    data_path, cache_path = get_data_path_cache_path(work_dir)
    sample_id = args["sampleId"]
    height = args["heightWidth"][0]
    width = args["heightWidth"][1]
    max_dim = max(height, width)
    thor.cell_gis(
        sample_id,
        max_dim,
        cell_selection=True
        )
    sample_id_area = args["sampleIdArea"]
    caches = cache_generate(sample_id, sample_id_area=sample_id_area)
    sample_id_area = args["sampleIdArea"]
    caches = cache_generate(sample_id, sample_id_area=sample_id_area)
    vio.copy(vio.join_path(cache_path,caches["gis-blend-cell-select-img"]),vio.join_path(cache_path,caches["gis-blend-cell-select-img-area"]))

def calculation_spot(folder_id, work_dir, vmin=None, vmax=None):
    """
    Perform spot gene analysis and caching.

    Parameters:
        folder_id: unique folder id for each sample

    Returns:
        None
    """
    thor, args, p_input_json = get_parameter(folder_id, work_dir)
    data_path, cache_path = get_data_path_cache_path(work_dir)
    sample_id = args['sampleId']
    selected_spot_gene_name = args['selectedSpotGeneName']
    sample_id_gene_spot = sample_id + "-" + selected_spot_gene_name

    thor.empty_cache_spot(sample_id, gene=True)

    args['sampleIdSpotGene'] = sample_id_gene_spot

    args['sampleIdSpotGene'] = sample_id_gene_spot

    vio.dump_json(args, f'{work_dir}/user{folder_id}/args.json')

    thor.spot_gis(
        sample_id=sample_id,
        selected_spot_gene_name=selected_spot_gene_name,
        vmin=vmin,
        vmax=vmax
    )
    if vmin is not None or vmax is not None:
        sample_id_min = args["sampleIdMin"]
        sample_id_max = args["sampleIdMax"]
        sample_id_minmax = args["sampleIdMinMax"]

        if vmin is not None and vmax is not None:
            cache = cache_generate(sample_id, sample_id_gene_minmax=sample_id_minmax)
            vio.copy(vio.join_path(cache_path, cache["gis-blend-spots"]), vio.join_path(cache_path, cache["gis-blend-spots-gene-minmax"]))
        elif vmax is not None:
            cache = cache_generate(sample_id, sample_id_gene_max=sample_id_max)
            vio.copy(vio.join_path(cache_path, cache["gis-blend-spots"]), vio.join_path(cache_path, cache["gis-blend-spots-gene-max"]))
        elif vmin is not None:
            cache = cache_generate(sample_id, sample_id_gene_min=sample_id_min)
            vio.copy(vio.join_path(cache_path, cache["gis-blend-spots"]), vio.join_path(cache_path, cache["gis-blend-spots-gene-min"]))
    else:
        cache = cache_generate(sample_id, sample_id_gene_spot=sample_id_gene_spot)
        vio.copy(vio.join_path(cache_path, cache["gis-blend-spots"]), vio.join_path(cache_path, cache["gis-blend-spots-gene"]))

    


# Alex add geojson_coords as a parameter
# 2/25/2025
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

    input_map = create_viv_viewer(
        'map-output',
        wsi_client,
        wsi_layer,
        [],
        cmax=0,
        geojson_coords=geojson_coords,
        token=token,
    )
    return input_map



# Alex add geojson_coords as a parameter
# 2/25/2025
def visualization_img_cell(folder_id, work_dir, cell_type=False, pathway=False, cell_detect=False, cell_select=False,min=False, max=False,
                           min_max=False, geojson_coords=None):
    """
    Visualize cell images including cell gene and optionally cell type.

    Parameters:
        folder_id: unique folder id for each sample
        data_path (str): The path to the data directory.
        cache_path (str): The path to the cache directory.
        cell_type (bool): Whether to include cell type visualization.

    Returns:
        obj: The output map object.
    """
    global all_gene_cell_layer
    global gene_cell
    global all_pathway_layer
    global pathway_cell
    data_path, cache_path = get_data_path_cache_path(work_dir)
    thor, args, p_input_json = get_parameter(folder_id, work_dir)
    selected_cell_gene_name = args['selectedCellGeneName']
    selected_pathway = args['selectedPathway']
    sample_id = args['sampleId']
    sample_id_file = args['sampleIdFile']
    sample_id_gene_cell = args['sampleIdCellGene']
    sample_id_pathway = args['sampleIdPathway']
    sample_id_area = args['sampleIdArea']

    user_token_info = get_user_token_info(work_dir, folder_id)
    server_port = user_token_info["tile-port"]
    token = user_token_info["user-token"]

    if len(all_gene_cell_layer) > 10:
        all_gene_cell_layer.clear()
        gene_cell.clear()
    if cell_type is True:
        wsi_client, wsi_layer = thor.gis_client_and_layer(sample_id_file, 'gis-wsi-img', server_port=server_port)
        cell_type_client, cell_type_layer = thor.gis_client_and_layer(sample_id_file, 'gis-blend-cell-type-img', server_port=server_port)
        output_map = create_viv_viewer(
            'map-output',
            wsi_client,
            wsi_layer,
            [(cell_type_client, 'cell type')],
            classes=thor.get_unique_cell_types(sample_id),
            geojson_coords=geojson_coords,
            token=token
        )
    
    elif pathway is True:
        cmap = args['cmap']["CMAP_PATHWAY"]
        wsi_client, wsi_layer = thor.gis_client_and_layer(sample_id_file, 'gis-wsi-img', server_port=server_port)
        globals()[f"cell_pathway_heatmap_client_{selected_pathway}"], globals()[f"cell_pathway_heatmap_layer_{selected_pathway}"] = thor.gis_client_and_layer(sample_id_pathway, 'gis-blend-cell-pathway-heatmap-img', server_port=server_port)
        if selected_pathway in pathway_cell:
            pass
        else:
            all_pathway_layer.append((globals()[f"cell_pathway_heatmap_client_{selected_pathway}"], f'{selected_pathway}'))
            pathway_cell.append(selected_pathway)
        output_map = create_viv_viewer(
            'map-output',
            wsi_client,
            wsi_layer,
            all_pathway_layer,
            cmax=thor.get_pathway_max(sample_id, selected_pathway),
            cmap = cmap,
            geojson_coords=geojson_coords,
            token=token,
        )

    elif cell_detect is True:
        cell_random_client, cell_random_layer = thor.gis_client_and_layer(sample_id_file, 'gis-blend-cell-random-img', server_port=server_port)
        wsi_client, wsi_layer = thor.gis_client_and_layer(sample_id_file, 'gis-wsi-img', server_port=server_port)
        output_map = create_viv_viewer(
            'map-output',
            wsi_client,
            wsi_layer,
            [(cell_random_client, 'cell detection')],
            cmax=0,
            geojson_coords=geojson_coords,
            token = token,
        )

    elif cell_select is True:
        cell_select_client, cell_select_layer = thor.gis_client_and_layer(sample_id_area, 'gis-blend-cell-select-img', server_port=server_port)
        wsi_client, wsi_layer = thor.gis_client_and_layer(sample_id_file, 'gis-wsi-img', server_port=server_port)
        output_map = create_viv_viewer(
            'map-output',
            wsi_client,
            wsi_layer,
            [(cell_select_client, 'cell selection')],
            cmax=0,
            geojson_coords=geojson_coords,
            token=token,
        )
        
    elif cell_type is False and pathway is False and cell_detect is False and cell_select is False:
        cmap = args['cmap']["CMAP_CELL_GENE"]
        # added when cmap is a ndarray
        if isinstance(cmap, list):
            cmap = np.array(cmap, dtype=np.uint8)

        sample_id_min = args["sampleIdMin"]
        sample_id_max = args["sampleIdMax"]
        sample_id_minmax = args["sampleIdMinMax"]
        if min_max is True:
            value = sample_id_minmax.split('-')
            value_min = value[-2]
            value_max = value[-1]
            _, globals()[f"cell_gene_layer_{selected_cell_gene_name}_minmax"] = thor.gis_client_and_layer(sample_id_minmax, 'gis-blend-cell-gene-img', server_port=server_port)
            wsi_client, wsi_layer = thor.gis_client_and_layer(sample_id_file, 'gis-wsi-img', server_port=server_port)
            all_gene_cell_layer.append((globals()[f"cell_gene_layer_{selected_cell_gene_name}_minmax"], f'{selected_cell_gene_name}_min{value_min}max{value_max}'))
            gene_cell.append(selected_cell_gene_name)
            output_map = create_viv_viewer(
                'map-output',
                wsi_client,
                wsi_layer,
                all_gene_cell_layer,
                cmax=thor.get_gene_max(sample_id, selected_cell_gene_name),
                cmap=cmap,
                geojson_coords=geojson_coords,
                token=token,
            )
        elif min is True:
            value = sample_id_min.split('-')
            value = value[-1]
            _, globals()[f"cell_gene_layer_{selected_cell_gene_name}_min"] = thor.gis_client_and_layer(sample_id_min, 'gis-blend-cell-gene-img', server_port=server_port)
            wsi_client, wsi_layer = thor.gis_client_and_layer(sample_id_file, 'gis-wsi-img', server_port=server_port)
            all_gene_cell_layer.append((globals()[f"cell_gene_layer_{selected_cell_gene_name}_min"], f'{selected_cell_gene_name}_min{value}'))
            gene_cell.append(selected_cell_gene_name)
            output_map = create_viv_viewer(
                'map-output',
                wsi_client,
                wsi_layer,
                all_gene_cell_layer,
                cmax=thor.get_gene_max(sample_id, selected_cell_gene_name),
                cmap=cmap,
                geojson_coords=geojson_coords,
                token=token,
            )
        elif max is True:
            value = sample_id_max.split('-')
            value = value[-1]
            _, globals()[f"cell_gene_layer_{selected_cell_gene_name}_max"] = thor.gis_client_and_layer(sample_id_max, 'gis-blend-cell-gene-img', server_port=server_port)
            wsi_client, wsi_layer = thor.gis_client_and_layer(sample_id_file, 'gis-wsi-img', server_port=server_port)
            all_gene_cell_layer.append((globals()[f"cell_gene_layer_{selected_cell_gene_name}_max"], f'{selected_cell_gene_name}_max{value}'))
            gene_cell.append(selected_cell_gene_name)
            output_map = create_viv_viewer(
                'map-output',
                wsi_client,
                wsi_layer,
                all_gene_cell_layer,
                cmax=thor.get_gene_max(sample_id, selected_cell_gene_name),
                cmap=cmap,
                geojson_coords=geojson_coords,
                token=token,
            )
        else:
            _, globals()[f"cell_gene_layer_{selected_cell_gene_name}"] = thor.gis_client_and_layer(sample_id_gene_cell, 'gis-blend-cell-gene-img', server_port=server_port)
            wsi_client, wsi_layer = thor.gis_client_and_layer(sample_id_file, 'gis-wsi-img', server_port=server_port)
            if selected_cell_gene_name in gene_cell:
                pass
            else:
                all_gene_cell_layer.append((globals()[f"cell_gene_layer_{selected_cell_gene_name}"], f'{selected_cell_gene_name}'))
                gene_cell.append(selected_cell_gene_name)
            output_map = create_viv_viewer(
                'map-output',
                wsi_client,
                wsi_layer,
                all_gene_cell_layer,
                cmax=thor.get_gene_max(sample_id, selected_cell_gene_name),
                cmap=cmap,
                geojson_coords=geojson_coords,
                token=token,
            )
        
    return output_map


# Alex add geojson_coords as a parameter
# 2/25/2025
def visualization_img_spot(folder_id, work_dir,min=False, max=False, min_max=False, geojson_coords=None):
    """
    Visualize spot images including spot gene.

    Parameters:
        folder_id: unique folder id for each sample
        data_path (str): The path to the data directory.
        cache_path (str): The path to the cache directory.

    Returns:
        obj: The output map object.
    """
    global all_gene_spot_layer
    global gene_spot
    data_path, cache_path = get_data_path_cache_path(work_dir)
    thor, args, p_input_json = get_parameter(folder_id, work_dir)
    sample_id = args['sampleId']
    selected_spot_gene_name = args['selectedSpotGeneName']
    sample_id_file = args['sampleIdFile']
    sample_id_gene_spot = args['sampleIdSpotGene']

    user_token_info = get_user_token_info(work_dir, folder_id)
    server_port = user_token_info["tile-port"]
    token = user_token_info["user-token"]

    if len(all_gene_spot_layer) > 10:
        all_gene_spot_layer.clear()
        gene_spot.clear()
    sample_id_min = args["sampleIdMin"]
    sample_id_max = args["sampleIdMax"]
    sample_id_minmax = args["sampleIdMinMax"]
    if min_max is True:
        value = sample_id_minmax.split('-')
        value_min = value[-2]
        value_max = value[-1]
        _, globals()[f"spot_gene_layer_{selected_spot_gene_name}_minmax"] = thor.gis_client_and_layer(sample_id_minmax, 'gis-blend-spot-gene-img', server_port=server_port)
        wsi_client, wsi_layer = thor.gis_client_and_layer(sample_id_file, 'gis-wsi-img', server_port=server_port)
        all_gene_spot_layer.append((globals()[f"spot_gene_layer_{selected_spot_gene_name}_minmax"], f'{selected_spot_gene_name}_min{value_min}max{value_max}'))
        gene_spot.append(selected_spot_gene_name)
        output_map = create_viv_viewer(
            'map-output',
            wsi_client,
            wsi_layer,
            all_gene_spot_layer,
            cmax=thor.get_spot_max(sample_id, selected_spot_gene_name,vmin=value_min,vmax=value_max),
            cmap='jet',
            geojson_coords=geojson_coords,
            token=token
        )
    elif min is True:
        value = sample_id_min.split('-')
        value = value[-1]
        _, globals()[f"spot_gene_layer_{selected_spot_gene_name}_min"] = thor.gis_client_and_layer(sample_id_min, 'gis-blend-spot-gene-img', server_port=server_port)
        wsi_client, wsi_layer = thor.gis_client_and_layer(sample_id_file, 'gis-wsi-img', server_port=server_port)
        all_gene_spot_layer.append((globals()[f"spot_gene_layer_{selected_spot_gene_name}_min"], f'{selected_spot_gene_name}_min{value}'))
        gene_spot.append(selected_spot_gene_name)
        output_map = create_viv_viewer(
            'map-output',
            wsi_client,
            wsi_layer,
            all_gene_spot_layer,
            cmax=thor.get_spot_max(sample_id, selected_spot_gene_name),
            cmap='jet',
            geojson_coords=geojson_coords,
            token=token, 
        )
    elif max is True:
        value = sample_id_max.split('-')
        value = value[-1]
        _, globals()[f"spot_gene_layer_{selected_spot_gene_name}_max"] = thor.gis_client_and_layer(sample_id_max, 'gis-blend-spot-gene-img', server_port=server_port)
        wsi_client, wsi_layer = thor.gis_client_and_layer(sample_id_file, 'gis-wsi-img', server_port=server_port)
        all_gene_spot_layer.append((globals()[f"spot_gene_layer_{selected_spot_gene_name}_max"], f'{selected_spot_gene_name}_max{value}'))
        gene_spot.append(selected_spot_gene_name)
        output_map = create_viv_viewer(
            'map-output',
            wsi_client,
            wsi_layer,
            all_gene_spot_layer,
            cmax=thor.get_spot_max(sample_id, selected_spot_gene_name),
            cmap='jet',
            geojson_coords=geojson_coords,
            token=token,
        )
    else:
        wsi_client, wsi_layer = thor.gis_client_and_layer(sample_id_file, 'gis-wsi-img', server_port=server_port)
        _, globals()[f"spot_gene_layer_{selected_spot_gene_name}"] = thor.gis_client_and_layer(sample_id_gene_spot, 'gis-blend-spot-gene-img', server_port=server_port)
        if selected_spot_gene_name in gene_spot:
            pass
        else:
            all_gene_spot_layer.append((globals()[f"spot_gene_layer_{selected_spot_gene_name}"], f'{selected_spot_gene_name}'))
            gene_spot.append(selected_spot_gene_name)
        output_map = create_viv_viewer(
            'map-output',
            wsi_client,
            wsi_layer,
            all_gene_spot_layer,
            cmax=thor.get_spot_max(sample_id, selected_spot_gene_name),
            cmap="jet",
            geojson_coords=geojson_coords,
            token=token,
        )
    return output_map


# zip folder
def zip_folder(folder_path, output_path):
    """
    Compresses the contents of a specified folder into a ZIP file.

    Parameters:
        folder_path (str): The path to the folder containing the files to be compressed.
        output_path (str): The path to the output ZIP file.
        
    Returns:
        None
    """
    with zipfile.ZipFile(output_path, 'w', zipfile.ZIP_DEFLATED) as zipf:
        for root, dirs, files in os.walk(folder_path):
            for file in files:
                file_path = os.path.join(root, file)
                arcname = os.path.relpath(file_path, folder_path)
                zipf.write(file_path, arcname)
  
                
def get_index(folder_id, work_dir, cell_gene=None, spot_gene=None):
    data_path, cache_path = get_data_path_cache_path(work_dir)
    thor, args, p_input_json = get_parameter(folder_id, work_dir)
    sample_id = args["sampleId"]
    if cell_gene is not None:
        files = files_generate(sample_id)
        cell_gene_list = pd.read_csv(os.path.join(data_path, files["cells-gene-names"]), header=None, index_col=0, sep="\t")
        cell_gene_list = list(cell_gene_list.index)
        idx = cell_gene_list.index(cell_gene)
    if spot_gene is not None:
        files = files_generate(sample_id)
        spot_gene_list = pd.read_csv(os.path.join(data_path, files["spots-gene-names"]), header=None, index_col=0, sep="\t")
        spot_gene_list = list(spot_gene_list.index)
        idx = spot_gene_list.index(spot_gene)
    return idx

# clear cache    
def clear_cache(folder_id, work_dir):
    """
    Clears cache files.

    Parameters:
        folder_id: unique folder id for each sample

    Returns:
        None or dash.no_update: If no cache files meet the conditions, returns dash.no_update.
            Otherwise, deletes cache files and returns None.
    """
    global all_gene_spot_layer
    global gene_spot
    global all_gene_cell_layer
    global gene_cell
    global all_pathway_layer
    global pathway_cell

    data_path, cache_path = get_data_path_cache_path(work_dir)
    thor, args, p_input_json = get_parameter(folder_id, work_dir)

    all_gene_spot_layer.clear()
    gene_spot.clear()
    all_gene_cell_layer.clear()
    gene_cell.clear()
    all_pathway_layer.clear()
    pathway_cell.clear()

    # sample_id = args['sampleId']
    files = os.listdir(cache_path)
    for del_file in files:
        if not del_file.startswith("gt-iz-p9-rep2-file-name-gis-wsi-img.tiff"):
        # and del_file.startswith(sample_id):
            file_path = os.path.join(cache_path, del_file)
            os.remove(file_path)


# # clear data
# def clear_data(folder_id, work_dir):
#     """
#     Clears previous data folder.

#     Parameters:
#         folder_id: unique folder id for each sample

#     Returns:
#         None or dash.no_update: If no cache files meet the conditions, returns dash.no_update.
#             Otherwise, deletes cache files and returns None.
#     """
#     data_path, cache_path = get_data_path_cache_path(work_dir)
#     thor, args, p_input_json = get_parameter(folder_id, work_dir)
#     # sample_id = args['sampleId']
#     # try:
#     #     shutil.rmtree(f'{work_dir}/user')
#     #     shutil.rmtree(f'{work_dir}/db')
#     #     shutil.rmtree(f'{work_dir}/data_input_temp')
#     # except FileNotFoundError:
#     #     pass
#     files = os.listdir(data_path)
#     for del_file in files:
#         if not del_file.startswith("gt-iz-p9-rep2"):
#         # and del_file.startswith(sample_id):
#             file_path = os.path.join(data_path, del_file)
#             os.remove(file_path)

#     try:
#         if folder_id == "":
#             pass
#         else:
#             shutil.rmtree(f"{work_dir}/user{folder_id}")
#     except FileNotFoundError:
#         pass
