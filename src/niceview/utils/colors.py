"""Color utilities."""

import numpy as np
import cv2
import matplotlib.pyplot as plt

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


