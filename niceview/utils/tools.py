"""Tools."""

from niceview.utils.cell import paint_regions
import numpy as np
from scipy.sparse import load_npz
import cv2
from shapely.geometry import Point, Polygon
import os
import copy
import matplotlib.pyplot as plt
import matplotlib as mpl
import re

import niceview.utils.io as vio

# Use /mnt/data (mounted EBS) for temp files on EC2; fall back to OS default elsewhere
TEMP_DIR = '/mnt/data' if os.path.exists('/mnt/data') else None

CMIN = 0
CMAX = 255



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


def list_to_txt(lines, txt_file):
    """Write lines of a list to a txt file.

    Args:
        lines (list of str): list of string of lines to be written
        txt_file (str): txt file path
    
    Returns:
        txt_file (str): txt file path
    """
    with open(txt_file, 'w') as txt:
        for line in lines:
            txt.write(line)
            txt.write('\n')
    return txt_file


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


def _vmax_vmin_gene_exp(arr, vmax=None , vmin=None):
    """
    Get vmin vmax for specific gene in adata

    Parameters:
        arr (arr): gene expression array for one gene
        gene (str): gene name
        vmax(str,float): vmax, you can use percentage (p99) or float
        vmin(str,float): vmin, you can use percentage (p0) or float
    Returns:
        adata
    """
    if vmin is not None and vmax is not None:
        if isinstance(vmin, float) and isinstance(vmax, float):
            vmin_q = vmin
            vmax_q =vmax
            min_max_q = np.clip(arr,a_max=vmax_q,a_min=vmin_q)
            return min_max_q
        else:
            vmin = re.search(r'p(\d+)', vmin)
            vmin = int(vmin.group(1))
            vmin = vmin / 100
            vmax = re.search(r'p(\d+)', vmax)
            vmax = int(vmax.group(1))
            vmax = vmax / 100
            vmin_q = np.quantile(arr, vmin)
            vmax_q = np.quantile(arr, vmax)
            min_max_q = np.clip(arr,a_max=vmax_q,a_min=vmin_q)
            return min_max_q
    elif vmax is not None:
        if isinstance(vmax, float):
            vmax_q =vmax
            vmax_q = np.clip(arr,a_max=vmax_q,a_min=arr.min())
            return vmax_q
        else:
            vmax = re.search(r'p(\d+)', vmax)
            vmax = int(vmax.group(1))
            vmax = vmax / 100
            vmax_q = np.quantile(arr, vmax)
            vmax_q = np.clip(arr,a_max=vmax_q,a_min=arr.min())
            return vmax_q
    elif vmin is not None :
        if isinstance(vmin, float): 
            vmin_q = vmin
            vmin_q = np.clip(arr,a_max=arr.max(),a_min=vmin_q)
            return vmin_q
        else:
            vmin = re.search(r'p(\d+)', vmin)
            vmin = int(vmin.group(1))
            vmin = vmin / 100
            vmin_q = np.quantile(arr, vmin)
            vmin_q = np.clip(arr,a_max=arr.max(),a_min=vmin_q)
            return vmin_q
    
    elif vmax is None or vmin is None:
        return arr


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


def mask_filter_relabel(mask_path, matched_regions, labels):
    """Filter mask by matched regions and relabel the mask.
    
    Args:
        mask_path (str): path to the mask file.
        matched_regions (list of int): list of matched regions.
        labels (list of int): list of labels.
        
    Returns:
        np.ndarray: filtered and relabeled mask.
    """
    mask = load_npz(mask_path)
    mask = mask.tocsr()[:, :].todense()
    # TODO: increase speed of `paint_regions`
    mask_filtered_relabeled = paint_regions(mask.shape, matched_regions, cell_colors_list=labels)
    return mask_filtered_relabeled.data


def get_hex_values(colormap_name):
    """Get hex values.
    
    Args:
        colormap_name (str or int): Colormap name in matplotlib or OpenCV.
    
    Returns:
        list[str]: List of hex values.
    """
    #cmap = plt.get_cmap(colormap_name)

    hex_values = []
    if isinstance(colormap_name, str):
        cmap = plt.get_cmap(colormap_name)
        for i in range(cmap.N):
            rgba = cmap(i)
            hex_color = '#{:02X}{:02X}{:02X}'.format(int(rgba[0] * CMAX), int(rgba[1] * CMAX), int(rgba[2] * CMAX))
            hex_values.append(hex_color)
    elif isinstance(colormap_name, int):
    # e.g. cv2.COLORMAP_JET
        cmap = cv2.applyColorMap(np.arange(256).reshape(256, 1).astype(np.uint8), colormap_name)
        # shape (256, 1, 3) 

        for i in range(cmap.shape[0]):
            bgr = cmap[i][0]
            hex_color = '#{:02X}{:02X}{:02X}'.format(int(bgr[2]), int(bgr[1]), int(bgr[0]))
            hex_values.append(hex_color)
    elif isinstance(colormap_name, np.ndarray):
        for i in range(colormap_name.shape[0]):
            bgr = colormap_name[i][0]
            hex_color = '#{:02X}{:02X}{:02X}'.format(int(bgr[2]), int(bgr[1]), int(bgr[0]))
            hex_values.append(hex_color)
    else:
        raise ValueError("colormap_name has to be a string, integer or numpy array.")
    return hex_values


def hex_to_rgb(hex_color):
    """Hexadecimal to RGB.
    
    Args:
        hex_color (str): hexadecimal color.
    
    Returns:
        tuple: RGB values.
    """
    # Remove the '#' symbol if it's present
    if hex_color.startswith('#'):
        hex_color = hex_color[1:]

    # Convert each pair from hexadecimal to decimal
    hex_max = 16
    r = int(hex_color[0:2], hex_max)
    g = int(hex_color[2:4], hex_max)
    b = int(hex_color[4:6], hex_max)

    # Return the RGB values as a tuple
    return (r, g, b)


def discrete_cmap_from_hex(id_to_hex_dict):
    """Discrete colormap from hex.
    
    Args:
        id_to_hex_dict (dict): dictionary of id to hex.
    
    Returns:
        np.ndarray: discrete colormap.
    """
    rgb_cmap = {int(k): hex_to_rgb(v) for k, v in id_to_hex_dict.items()}
    rgb_cmap = np.array([rgb_cmap[i] for i in range(1, len(rgb_cmap) + 1)])
    bgr_cmap = rgb_cmap[:, ::-1]
    return bgr_cmap


def apply_custom_cmap(img_gray, cmap):
    """Apply custom colormap to gray image.
    
    Args:
        img_gray (np.ndarray): gray image.
        cmap (np.ndarray): custom colormap.
    
    Returns:
        np.ndarray: colored image.
    """
    lut = np.zeros((256, 1, 3), dtype=np.uint8)
    # rgb
    lut[1: len(cmap) + 1, 0, 0] = cmap[:, 0]
    lut[1: len(cmap) + 1, 0, 1] = cmap[:, 1]
    lut[1: len(cmap) + 1, 0, 2] = cmap[:, 2]
    # apply
    img_rgb = cv2.LUT(img_gray, lut)
    return img_rgb

def matplotlib_cmap_to_numpy(cmap):
    """Convert cmap to numpy.
    
    Args:
        cmap (str): colormap.
    
    Returns:
        np.ndarray: color_map.
    """
    cmap = plt.get_cmap(cmap)

    # Create a range of values from 0 to 255
    color_range = np.linspace(0, 255, 255)

    # Normalize the range to 0-1 as expected by matplotlib colormaps
    norm_color_range = color_range / 255.0

    # Apply the colormap to the normalized range
    color_map = cmap(norm_color_range) * 255

    # Convert to uint8
    color_map = color_map.astype(np.uint8)

    #change RGB to BGR
    color_map[:,0], color_map[:,2] = color_map[:,2], color_map[:,0].copy()

    return color_map


def get_cmap(cmap_name, rgb_order=False):
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

    c_map = mpl.cm.get_cmap(cmap_name, 256)
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


def mask_to_image(mask, cmap):
    """Convert mask to image.
    
    Args:
        mask (np.ndarray): mask.
        cmap (int or str, np.array): colormap. If int, use OpenCV colormap. If str, use matplotlib colormap. If np.array, use custom colormap in
        the shape of (256, 1, 3) or (256, 1, 4) [BGR or BGRA].
    
    Returns:
        np.ndarray: image.
    """
#    print(type(cmap))
    if isinstance(cmap, int):
        # TODO: increase speed of the following three lines, as they are "overlapping"
#        img_rgb = cv2.cvtColor(mask.astype(np.uint8), cv2.COLOR_BGR2RGB)
#        img_rgb = cv2.applyColorMap(img_rgb, cmap)
        img_rgb = cv2.applyColorMap(mask.astype(np.uint8), cmap)
        img_rgb = cv2.bitwise_and(img_rgb, img_rgb, mask=mask.astype(np.uint8))
    elif isinstance(cmap, str):
#        img_gray = cv2.cvtColor(mask.astype(np.uint8), cv2.COLOR_GRAY2BGR)
#        color_map = matplotlib_cmap_to_numpy(cmap)
#        color_map[0] = [255, 255, 255, 255]
#        img_rgb = apply_custom_cmap(img_gray, color_map)
        img_rgb = cv2.applyColorMap(mask.astype(np.uint8), get_cmap(cmap))
        img_rgb = cv2.bitwise_and(img_rgb, img_rgb, mask=mask.astype(np.uint8))
    elif isinstance(cmap, np.ndarray):
#        img_gray = cv2.cvtColor(mask.astype(np.uint8), cv2.COLOR_GRAY2BGR)
#        img_rgb = apply_custom_cmap(img_gray, cmap)

        img_rgb = cv2.applyColorMap(mask.astype(np.uint8), cmap.astype(np.uint8))
        img_rgb = cv2.bitwise_and(img_rgb, img_rgb, mask=mask.astype(np.uint8))
    else:
        raise ValueError("cmap has to be an integer, a string, or a numpy array")

    return img_rgb


def mask_to_image_discrete(mask, cmap):
    """Convert mask to image with discrete colormap.
    representing different cell type labels or selection labels.
    
    Args:
        mask (np.ndarray): mask.
        cmap (np.ndarray): discrete colormap. shape (ntypes, 3).
    
    Returns:
        np.ndarray: image.
    """
    img_gray = cv2.cvtColor(mask.astype(np.uint8), cv2.COLOR_GRAY2BGR)
    img_rgb = apply_custom_cmap(img_gray, cmap)

    return img_rgb


def draw_circles(img_shape, centers, diameter, colors, cmap=cv2.COLORMAP_JET, thickness=-1):
    """Draw circles on image.
    
    Args:
        img_shape (tuple): image shape.
        centers (list of tuple): list of centers.
        diameter (list of int): list of diameters.
        colors (np.ndarray): colors.
        cmap (int or np.ndarray): colormap.
        thickness (int): thickness of the circle.
    
    Returns:
        np.ndarray: image with circles.
    """
    # black background
    canvas = np.zeros((img_shape[0], img_shape[1], 3))
    
    # color
    if isinstance(cmap, int):
        colors = cv2.cvtColor(colors.astype(np.uint8), cv2.COLOR_BGR2RGB)
        colors = cv2.applyColorMap(colors, cv2.COLORMAP_JET)
        colors = np.reshape(colors, (-1, 3))
    else:
        colors = cv2.cvtColor(colors.astype(np.uint8), cv2.COLOR_BGR2RGB)
        colors = apply_custom_cmap(colors, cmap)
        colors = np.reshape(colors, (-1, 3))

    # set diameter
    if isinstance(diameter, int):
        diameter = [diameter] * len(centers)
    
    # draw circles
    for center, d, color in zip(centers, diameter, colors):
        color = tuple(map(int, color))  # convert elements to int
        center = np.round(center).astype('int')
        radius = np.round(d / 2).astype('int')
        cv2.circle(canvas, center, radius, color, thickness)
    return canvas


# TODO: speed up `blend`
def blend(img_path, mask_path, mask_opacity, heatmap=False):
    """Blend mask and image.
    
    Args:
        img_path (str): path to the image.
        mask_path (str): path to the mask.
        mask_opacity (float): opacity of the mask.
    
    Returns:
        np.ndarray: blended image.
    """
    opacity = 0.5
    mask_img = vio.read_image_cv2(mask_path)
    bkgd_img = vio.read_image_cv2(img_path)
    if heatmap is False:
        blank_background = np.zeros_like(bkgd_img, dtype=np.uint8) + 255
        bkgd_img = cv2.addWeighted(bkgd_img, 1-opacity, blank_background, opacity, 0)

    
    # blend part of background
    mask = cv2.cvtColor(mask_img, cv2.COLOR_BGR2GRAY)
    bkgd_blend = cv2.bitwise_and(bkgd_img, bkgd_img, mask=mask)
    
    # non-blend part of background
    inv_mask = (mask == 0).astype(np.uint8)
    bkgd_non_blend = cv2.bitwise_and(bkgd_img, bkgd_img, mask=inv_mask)
    
    mask_ovelay = cv2.addWeighted(mask_img, mask_opacity, bkgd_blend, 1.0 - mask_opacity, 0)
    whole_img = cv2.addWeighted(mask_ovelay, 1.0, bkgd_non_blend, 1.0, 0)
    return whole_img


def get_bounding_box(coords):
    """Get bounding box of coordinates.
    
    Args:
        coords (list of tuple): list of coordinates.
    
    Returns:
        tuple: bounding box.
    """
    x_coords, y_coords = zip(*coords)
    x1, y1 = min(x_coords), min(y_coords)
    x2, y2 = max(x_coords), max(y_coords)
    return x1, y1, x2, y2


def save_roi_data_img(coords, adata, img, home_dir, resized_coords=None, spot_cell="cell"):
    """Get roi from coordinates.
    
    Args:
        coords (list of tuple): original coords.
        adata (anndata.AnnData): anndata.
        img (np.ndarray): image.
        home_dir (str): home directory.
        coords_temp (list of tuple): list of coordinates for rois in the rescaled image. (if the image is not rescaled, it is the same as coords)
    """
    for idx, coord in enumerate(coords):
        
        if adata is not None:
            # because adata input can be original or regized so this does not matter
            # the coords and adata will always be the same scale
            roi = Polygon(coord)
            locs = list(map(lambda x: roi.contains(Point(x)), adata.obsm['spatial']))
            to_keep = adata[locs].copy()
            if spot_cell == "cell":
                h5ad_path = os.path.join(home_dir, f'roi-{idx}.h5ad')
                if vio.is_s3(h5ad_path):
                     # Write to temp file then upload
                     import tempfile
                     with tempfile.NamedTemporaryFile(suffix=".h5ad", delete=False, dir=TEMP_DIR) as tmp:
                         to_keep.write_h5ad(tmp.name)
                         tmp_path = tmp.name
                     vio.copy(tmp_path, h5ad_path)
                     os.remove(tmp_path)
                else:
                    to_keep.write_h5ad(h5ad_path)
            else:
                h5ad_path = os.path.join(home_dir, f'roi-{idx}-spot.h5ad')
                if vio.is_s3(h5ad_path):
                     import tempfile
                     with tempfile.NamedTemporaryFile(suffix=".h5ad", delete=False, dir=TEMP_DIR) as tmp:
                         to_keep.write_h5ad(tmp.name)
                         tmp_path = tmp.name
                     vio.copy(tmp_path, h5ad_path)
                     os.remove(tmp_path)
                else:
                    to_keep.write_h5ad(h5ad_path)

    for idx, coord_tmp in enumerate(resized_coords):    
        # save image
        # save region image based on resized_coords
        # the wsi is still resized to 10k
        x1, y1, x2, y2 = get_bounding_box(coord_tmp)
        pts = np.array(coord_tmp, np.int32).reshape((-1, 1, 2))
        img_copy = copy.deepcopy(img)
        cv2.polylines(img_copy, [pts], isClosed=True, color=(255, 0, 0), thickness=4)
        cropped_region = img_copy[y1:y2, x1:x2]
        

        # save big images with marked the region
        cv2.polylines(img_copy, [pts], isClosed=True, color=(255, 0, 0), thickness=20)
        height, width, _ = img_copy.shape
        max_dim = max(height, width)
        resize_factor = 2000 / max_dim
        resized_img_marked = cv2.resize(img_copy, (int(width * resize_factor), int(height * resize_factor)))
        

        # save whole image without marked
        img_non = copy.deepcopy(img)    
        resized_img_non = cv2.resize(img_non, (int(width * resize_factor), int(height * resize_factor)))

        if spot_cell == "cell":
            vio.write_image(os.path.join(home_dir, f'roi-{idx}.jpeg'), cropped_region)
            vio.write_image(os.path.join(home_dir, f'whole-marked-{idx}.jpeg'), resized_img_marked)
            vio.write_image(os.path.join(home_dir, f'whole.jpeg'), resized_img_non)
        else:
            vio.write_image(os.path.join(home_dir, f'roi-{idx}-spot.jpeg'), cropped_region)
            vio.write_image(os.path.join(home_dir, f'whole-marked-{idx}-spot.jpeg'), resized_img_marked)
            vio.write_image(os.path.join(home_dir, f'whole-spot.jpeg'), resized_img_non)


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
