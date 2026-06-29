"""Rendering utilities."""

import numpy as np
import cv2
import os
from scipy.sparse import load_npz

from niceview.utils.cell import paint_regions
import niceview.utils.io as vio

try:
    from niceview.utils.colors import get_cmap, apply_custom_cmap
except ImportError:
    from colors import get_cmap, apply_custom_cmap


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


def mask_to_image(mask, cmap):
    """Convert mask to image.

    Args:
        mask (np.ndarray): mask.
        cmap (int or str, np.array): colormap. If int, use OpenCV colormap. If str, use matplotlib colormap. If np.array, use custom colormap in
        the shape of (256, 1, 3) or (256, 1, 4) [BGR or BGRA].

    Returns:
        np.ndarray: image.
    """
    if isinstance(cmap, int):
        img_rgb = cv2.applyColorMap(mask.astype(np.uint8), cmap)
        img_rgb = cv2.bitwise_and(img_rgb, img_rgb, mask=mask.astype(np.uint8))
    elif isinstance(cmap, str):
        img_rgb = cv2.applyColorMap(mask.astype(np.uint8), get_cmap(cmap))
        img_rgb = cv2.bitwise_and(img_rgb, img_rgb, mask=mask.astype(np.uint8))
    elif isinstance(cmap, np.ndarray):
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
