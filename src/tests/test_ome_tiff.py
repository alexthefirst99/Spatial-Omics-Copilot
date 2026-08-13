from __future__ import annotations

import xml.etree.ElementTree as ET

import numpy as np
import tifffile
from PIL import Image

from dash_viv_viewer import convert_to_ome_tiff


def test_rgb_conversion_declares_all_interleaved_samples(tmp_path):
    source = tmp_path / "rgb.png"
    output = tmp_path / "rgb.ome.tiff"
    Image.fromarray(np.zeros((32, 48, 3), dtype=np.uint8), mode="RGB").save(source)

    convert_to_ome_tiff(str(source), str(output))

    with tifffile.TiffFile(output) as tif:
        root = ET.fromstring(tif.ome_metadata)
        pixels = root.find(".//{*}Pixels")
        channel = root.find(".//{*}Channel")

        assert pixels is not None
        assert channel is not None
        assert pixels.attrib["SizeC"] == "3"
        assert pixels.attrib["Interleaved"] == "true"
        assert channel.attrib["SamplesPerPixel"] == "3"
        assert tif.series[0].shape == (32, 48, 3)

