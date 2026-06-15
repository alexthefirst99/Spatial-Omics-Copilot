"""Cell."""

import numpy as np
import skimage.measure
from sklearn.neighbors import NearestNeighbors


def get_nuclei_centroids(cm):
    """Get nuclei centroids.
    
    Args:
        cm (np.ndarray): Cell mask.
    
    Returns:
        tuple: Nuclei centroids.
    """
    regions = skimage.measure.regionprops(cm)
    xy = np.array([r.centroid[::-1] for r in regions])
    return xy, regions


def get_nuclei_pixels(cm, ad_cell_pos=None, tol=1e-3, ad_cell_label=None):
    """Get nuclei pixels.
    
    Args:
        cm (np.ndarray): Cell mask.
        ad_cell_pos (np.ndarray): Adherent cell positions.
        tol (float): Tolerance for matching cell and nucleus centroid.
        ad_cell_label (np.ndarray): Adherent cell label. If present and valid, ad_cell_pos and tol are ignored.

    Returns:
        list: Nuclei region pixels.

    Note:
        By PZhang, the ad_cell_label turns out to be a list of nans reading from "cell-info". Here we consider this case as no seg label.
    """

    # this is always true for the current implementation
    if ad_cell_label is not None:
        INVALID_SEG_LABEL = np.any(np.isnan(ad_cell_label))
        if not INVALID_SEG_LABEL:
            return get_nuclei_pixels_from_label(cm, ad_cell_label)

    xy, regions = get_nuclei_centroids(cm)

    if ad_cell_pos is not None:
#        print("I'm HERE")
#        print(ad_cell_pos)
#        print(xy)

#        np.savetxt("XY.txt", xy)
#        np.savetxt("ad_cell_pos.txt", ad_cell_pos)

        ## Alex added here May 21 2025
        #regions = skimage.measure.regionprops(cm)
        #xy = np.array([r.centroid[::-1] for r in regions])



        nbrs = NearestNeighbors(n_neighbors=1).fit(xy)
        distance, indices = nbrs.kneighbors(ad_cell_pos)
        c_index = indices[np.where(distance <= tol)]

        nuclei_region_pixels = []
        for n in indices[:, 0]:
            if n in c_index:
                nuclei_region_pixels.append((regions[n].coords[:, 0], regions[n].coords[:, 1]))
            else:
                nuclei_region_pixels.append(([], []))

        perfect_match = np.where(distance <= tol)[0]

        if len(perfect_match) != len(ad_cell_pos):
            print(f"Only {len(perfect_match)} out of {len(ad_cell_pos)} cells have perfect match with nuclei.")

    else:
        #xy, regions = get_nuclei_centroids(cm)
        nuclei_region_pixels = [
            (region.coords[:, 0], region.coords[:, 1]) for region in regions
        ]

#    print("MATCHED: ", len(nuclei_region_pixels))
    return np.array(nuclei_region_pixels, dtype=object)


def get_nuclei_pixels_from_label(cm, ad_cell_label):
    """Get nuclei pixels from label.
        
    Args:
        cm (np.ndarray): Cell mask.
        ad_cell_label (np.ndarray): Adherent cell label.

    Returns:
        list: Nuclei region pixels.

    Note:
        Label should be from 1 to n_cells (with skips). However if a rescaled cm is used, there is a chance that the ad_cell_label is not present in cm.
    """

    ad_cell_label = ad_cell_label.astype(int)
    # Label should be from 1 to n_cells
    assert np.all(ad_cell_label > 0), "Label should be from 1 to n_cells"

    regions = skimage.measure.regionprops(cm)
    seg_label_indices = {r.label:i for i, r in enumerate(regions)}


    nuclei_pixels_list = []
    for l in ad_cell_label:
        if l not in seg_label_indices:
            nuclei_pixels_list.append(([], []))
        else:
            nuclei_pixels_list.append((regions[seg_label_indices[l]].coords[:, 0], regions[seg_label_indices[l]].coords[:, 1]))

    return np.array(nuclei_pixels_list, dtype=object)


def paint_regions(image_shape, matched_regions, cell_colors_list):
    """Paint regions.
    
    Args:
        image_shape (tuple): Image shape.
        matched_regions (list): Matched regions.
        cell_colors_list (list): Cell colors list.
    
    Returns:
        np.ndarray: Filled image.
    """
    filled = np.ma.masked_all(image_shape)
    for i, r in enumerate(matched_regions):
        cc, rr = r
        filled[cc, rr] = cell_colors_list[i]
    return filled
