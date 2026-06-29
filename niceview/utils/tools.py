"""Tools."""

import numpy as np
from scipy.sparse import load_npz
import os

import niceview.utils.io as vio

# Use /mnt/data (mounted EBS) for temp files on EC2; fall back to OS default elsewhere
TEMP_DIR = '/mnt/data' if os.path.exists('/mnt/data') else None


def txt_to_list(txt_file):
    """Read lines of a txt file to a list.

    Args:
        txt_file (str): txt file path

    Returns:
        lines (list of str): list of string of lines in the txt file
    """
    with open(txt_file, 'r') as txt:
        lines = txt.readlines()
        lines = [line.strip() for line in lines]
    return lines


def select_col_from_name(matrix, name_list, name):
    """Select column from matrix by name.

    Args:
        matrix (np.ndarray): matrix of shape (row, col).
        name_list (list): list of names.
        name (str): name to select.

    Returns:
        np.ndarray: column of shape (row,).
    """
    idx = name_list.index(name)
    if isinstance(matrix, np.ndarray) and matrix.ndim == 2:
        return matrix[:, idx]
    return matrix.tocsr()[:, idx].todense()


def normalize_array(arr, new_min, new_max, vmin=None, vmax=None):
    """Normalize array to [new_min, new_max].

    Args:
        arr (np.ndarray): array to be normalized.
        new_min (float): new minimum value.
        new_max (float): new maximum value.

    Returns:
        np.ndarray: normalized array.
    """
    arr = np.array(arr)
    arr = vmax_vmin_gene_exp(arr, vmin=vmin, vmax=vmax)
    min_val = np.min(arr)
    max_val = np.max(arr)
    normalized_arr = (arr - min_val) / (max_val - min_val) * (new_max - new_min) + new_min
    return normalized_arr


def quantile_to_number(input_str, arr):
    """ Convert quantile to number
    Args:
        input_str (str): input string, which has to be 'p' followed by a number
        arr (np.ndarray): 1D expression array of a gene
    Returns:
        float: quantile value

    By Pengzhi Zhang
    """

    assert isinstance(input_str, str), "input_str has to be 'p' followed by a number between 0 and 100"

    q = float(input_str[1:])
    q = 0.01 * q
    number = np.quantile(arr, q)
    return number


def vmax_vmin_gene_exp(arr, vmin=None, vmax=None):
    """ Clip gene expression array based on vmax and vmin
    Args:
        arr (np.ndarray): 1D expression array of a gene
        vmin (None, str, or numerical): minimum value, if None, no clipping
        vmax (None, str, or numerical): maximum value, if None, no clipping
    Returns:
        varr (np.ndarray): clipped array

    By Pengzhi Zhang
    """

    if vmin is None and vmax is None:
        return arr

    if isinstance(vmin, str):
        vmin = quantile_to_number(vmin, arr)

    if isinstance(vmax, str):
        vmax = quantile_to_number(vmax, arr)

    varr = np.clip(arr, a_min=vmin, a_max=vmax)
    return varr


from niceview.utils.colors import *   # noqa: F401,F403
from niceview.utils.rendering import *  # noqa: F401,F403
