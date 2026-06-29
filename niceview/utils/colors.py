"""Color utilities."""

import numpy as np
import cv2
import matplotlib.pyplot as plt
import matplotlib as mpl

CMIN = 0
CMAX = 255


def get_hex_values(colormap_name):
    """Get hex values.

    Args:
        colormap_name (str or int): Colormap name in matplotlib or OpenCV.

    Returns:
        list[str]: List of hex values.
    """
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
