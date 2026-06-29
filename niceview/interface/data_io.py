import os
import niceview.utils.io as vio
from niceview.utils.dataset import ThorQuery


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
