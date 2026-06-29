import niceview.utils.io as vio


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


from niceview.interface.data_io import *      # noqa: F401,F403
from niceview.interface.visualization import *  # noqa: F401,F403
