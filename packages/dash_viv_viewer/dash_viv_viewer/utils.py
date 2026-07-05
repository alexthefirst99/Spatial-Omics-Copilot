import os
import tifffile
import numpy as np
from PIL import Image

# Suppress DecompressionBombWarning for very large images
Image.MAX_IMAGE_PIXELS = None

def _retag_photometric(path, photometric_value):
    patched = 0
    with tifffile.TiffFile(path, mode="r+") as tif:
        pages = [tif.pages[0], *list(tif.pages[0].pages)]
        for page in pages:
            photometric = page.tags.get("PhotometricInterpretation")
            if photometric:
                photometric.overwrite(photometric_value)
                patched += 1
    return patched

def _tiff_tag_value(page, tag_name):
    tag = page.tags.get(tag_name)
    return tag.value if tag else None

def _debug_tiff_tags(path, label):
    try:
        with tifffile.TiffFile(path) as tif:
            pages = [tif.pages[0], *list(tif.pages[0].pages)]
            print(f"[dash_viv_viewer] {label}: {len(pages)} TIFF levels", flush=True)
            for idx, page in enumerate(pages[:5]):
                print(
                    f"[dash_viv_viewer] {label} level {idx}: "
                    f"shape={page.shape}, "
                    f"photometric={_tiff_tag_value(page, 'PhotometricInterpretation')}, "
                    f"compression={_tiff_tag_value(page, 'Compression')}, "
                    f"samples={_tiff_tag_value(page, 'SamplesPerPixel')}, "
                    f"planar={_tiff_tag_value(page, 'PlanarConfiguration')}, "
                    f"ycbcr_subsampling={_tiff_tag_value(page, 'YCbCrSubSampling')}",
                    flush=True,
                )
    except Exception as exc:
        print(f"[dash_viv_viewer] Failed to inspect TIFF tags ({label}): {exc}", flush=True)

def _libvips_version(pyvips):
    return tuple(pyvips.version(i) for i in range(3))

def convert_to_ome_tiff(
    input_path,
    output_path=None,
    tile_size=256,
    max_levels=None,
    compression="auto",
    jpeg_quality=90,
):
    """
    Ultra-fast OME-TIFF pyramid generation using pyvips.
    Reads an ordinary flat image (PNG, JPG, TIFF) or WSI and converts it to a tiled, 
    pyramidal format optimized for performance in the Viv WebGL viewer.
    """
    import pyvips
    import uuid
    input_path = os.path.abspath(input_path)
    
    if output_path is None:
        base, _ = os.path.splitext(input_path)
        output_path = base + ".ome.tif"
    
    print(f"[dash_viv_viewer] Loading image with pyvips: {input_path}")
    
    # Streaming load via pyvips (handles SVS, huge TIFFs, PNG, etc without OOM)
    img = pyvips.Image.new_from_file(input_path, access="sequential")
    
    # Preserve RGBA overlays. Older code dropped alpha, which made transparent
    # cluster layers cover the base histology image.

    # VivViewer requires OME-XML metadata in the ImageDescription tag.
    # Otherwise it fails with TypeError: Cannot read properties of undefined (reading 'replace')
    vips_format_map = {
        'uchar': 'uint8', 'char': 'int8',
        'ushort': 'uint16', 'short': 'int16',
        'uint': 'uint32', 'int': 'int32',
        'float': 'float', 'double': 'double',
    }
    ome_type = vips_format_map.get(img.format, 'uint8')
    is_rgb = (img.bands in (3, 4))
    vips_version = _libvips_version(pyvips)

    if compression == "auto" and is_rgb and ome_type == "uint8" and img.bands == 3:
        save_compression = "jpeg"
    elif compression == "auto" and img.bands == 4:
        save_compression = "deflate"
    else:
        save_compression = compression
    if save_compression == "auto":
        save_compression = "deflate"
    
    if is_rgb:
        channel_xml = f'<Channel ID="Channel:0:0" SamplesPerPixel="{img.bands}"><LightPath/></Channel>'
        ome_pixels_attrs = f'SizeC="1" SizeT="1" SizeX="{img.width}" SizeY="{img.height}" SizeZ="1" Type="{ome_type}" Interleaved="true"'
    else:
        channel_xml = '<Channel ID="Channel:0:0" SamplesPerPixel="1"><LightPath/></Channel>'
        ome_pixels_attrs = f'SizeC="1" SizeT="1" SizeX="{img.width}" SizeY="{img.height}" SizeZ="1" Type="{ome_type}"'

    ome_xml = f'<?xml version="1.0" encoding="UTF-8"?><OME xmlns="http://www.openmicroscopy.org/Schemas/OME/2016-06" xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance" xsi:schemaLocation="http://www.openmicroscopy.org/Schemas/OME/2016-06 http://www.openmicroscopy.org/Schemas/OME/2016-06/ome.xsd" UUID="urn:uuid:{uuid.uuid4()}"><Image ID="Image:0" Name="Image0"><Pixels ID="Pixels:0" DimensionOrder="XYCZT" {ome_pixels_attrs}>{channel_xml}<TiffData IFD="0" PlaneCount="1"/></Pixels></Image></OME>'

    img.set_type(pyvips.GValue.gstr_type, 'image-description', ome_xml)
        
    print(
        f"[dash_viv_viewer] Writing ultra-fast TIFF pyramid to: {output_path} "
        f"(compression={save_compression})"
    )
    
    # Pyvips natively generates sub-resolutions, tiles them, and writes to BigTIFF
    # directly using libtiff in C, making this step almost instantaneous compared to PIL.
    save_kwargs = {
        "tile": True,
        "tile_width": tile_size,
        "tile_height": tile_size,
        "pyramid": True,
        "compression": save_compression,
        "bigtiff": True,
        "subifd": True,
        # Nearest is the fastest pyramid downsample mode. The base layer remains
        # unchanged; only lower zoom levels trade a little smoothness for speed.
        "region_shrink": "nearest",
    }
    if save_compression == "jpeg":
        save_kwargs["Q"] = jpeg_quality

    img.tiffsave(output_path, **save_kwargs)
    if save_compression == "jpeg" and is_rgb:
        if vips_version < (8, 17, 0):
            _debug_tiff_tags(output_path, "before retag")
            patched = _retag_photometric(output_path, 6)
            _debug_tiff_tags(output_path, "after retag")
            print(
                "[dash_viv_viewer] Retagged JPEG TIFF as YCbCr for "
                f"libvips {vips_version[0]}.{vips_version[1]}.{vips_version[2]} "
                f"({patched} levels)"
            )
        else:
            print(
                "[dash_viv_viewer] Keeping JPEG TIFF as RGB for "
                f"libvips {vips_version[0]}.{vips_version[1]}.{vips_version[2]}"
            )
    
    print(f"[dash_viv_viewer] Finished conversion: {output_path}")
    return output_path

def serve_directory(directory_path, port=5001, host="127.0.0.1"):
    """
    Starts a lightweight, background Flask server to serve local files from a directory 
    over HTTP with CORS and HTTP Range-Requests enabled (required for Viv).
    
    Parameters:
    -----------
    directory_path : str
        Absolute or relative path to the folder containing your OME-TIFF images.
    port : int, default=5001
        The port to host the images on.
    host : str, default="127.0.0.1"
        The host address.
    
    Returns:
    --------
    server_url : str
        The base URL of the running server, e.g., "http://127.0.0.1:5001"
    """
    import threading
    from flask import Flask, send_from_directory, request
    from flask_cors import CORS

    abs_directory = os.path.abspath(directory_path)
    if not os.path.isdir(abs_directory):
        raise ValueError(f"Directory not found: {abs_directory}")

    image_app = Flask('dash_viv_image_server')
    CORS(image_app)  # Viv strictly requires CORS headers to fetch tiles

    @image_app.route('/<path:filename>')
    def serve_file(filename):
        # 'conditional=True' is absolutely critical! 
        # It enables HTTP 206 Partial Content (Range requests) which Viv uses to stream the OME-TIFF.
        # Strip query parameters (like ?t=123) that might have been accidentally baked into the path by frontend clients
        if "?" in filename:
            filename = filename.split("?")[0]
        return send_from_directory(abs_directory, filename, conditional=True)

    def run_app():
        image_app.run(host=host, port=port, debug=False, use_reloader=False)

    server_thread = threading.Thread(target=run_app, daemon=True)
    server_thread.start()
    
    base_url = f"http://{host}:{port}"
    print(f"[dash_viv_viewer] Built-in Image Server running at: {base_url}/<filename>")
    print(f"[dash_viv_viewer] Serving directory: {abs_directory}")
    
    return base_url
