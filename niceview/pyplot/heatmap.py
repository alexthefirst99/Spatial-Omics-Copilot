"""Heatmap."""

import cv2
import numpy as np
from niceview.utils.tools import mask_to_image
import pandas as pd


def heatmap_from_scatter(
    img_size,
    coords,
    normalized_scores,
    dst_path,
    patch_size=(32, 32),
    overlap=0,
    cmap=cv2.COLORMAP_JET,
):
    """Generate heatmap.
    
    Args:
        dst_path (str): Path to save heatmap.
        img_size (tuple): Size of the image.
        patch_size (tuple): Size of the patch.
        coords (np.array): List of coordinates.
        normalized_scores (np.array): List of normalized scores.
        cmap (cv2.COLORMAP_*): Colormap.
        overlap (float): Overlap between patches.
    """
    width, height = img_size
    patch_size = np.ceil(np.array(patch_size)).astype(int)
    overlay = np.full((height, width), 0).astype(float)
    counter = np.full((height, width), 0).astype(np.uint16)
    for idx in range(len(coords)):
        score = normalized_scores[idx]
        coord = coords[idx]  # coord in (x, y)
        
        overlay[coord[1]- patch_size[1]: coord[1] + patch_size[1], coord[0]- patch_size[0]: coord[0] + patch_size[0]] += score
        counter[coord[1]- patch_size[1]: coord[1] + patch_size[1], coord[0]- patch_size[0]: coord[0] + patch_size[0]] += 1
    zero_mask = counter == 0
    overlay[~zero_mask] = overlay[~zero_mask] / counter[~zero_mask]
    
    # Modify overlay to make empty regions white
    overlay[zero_mask] = 0   # Set empty regions to white

    kernel = tuple((patch_size * (1 - overlap)).astype(int) * 2 + 1)
    img = mask_to_image(overlay, cmap=cmap)
    blur = cv2.GaussianBlur(
        img, kernel, 0,
    )
    cv2.imwrite(dst_path, blur)
