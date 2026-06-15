"""functions mainly based on rasterio."""

import os
import numpy as np
import rasterio
from rasterio.crs import CRS
from rasterio.warp import calculate_default_transform, reproject, Resampling
import warnings
import tempfile
import boto3
from botocore import UNSIGNED
from botocore.config import Config
import niceview.utils.io as vio

MAX_PIXEL_VAL = 255
# Use /mnt/data for temp files if available (EC2 optimization)
TEMP_DIR = '/mnt/data' if os.path.exists('/mnt/data') else None


def rgba2rgb(rgba):
    """Convert RGBA image to RGB.

    Args:
        rgba (np.ndarray): RGBA image array.

    Returns:
        np.ndarray: RGB image array.
        
    Raises:
        ValueError: if input image does not have 4 channels.
    """
    ch, row, col = rgba.shape
    if ch != 4:
        raise ValueError('Input image must have 4 channels.')
    rgb = np.zeros((3, row, col), dtype='float32')
    r, g, b, a = (
        rgba[0, :, :], rgba[1, :, :], rgba[2, :, :], rgba[3, :, :],
    )

    a = np.asarray(a, dtype='float32') / MAX_PIXEL_VAL

    rgb[0, :, :] = r * a + (1.0 - a) * MAX_PIXEL_VAL
    rgb[1, :, :] = g * a + (1.0 - a) * MAX_PIXEL_VAL
    rgb[2, :, :] = b * a + (1.0 - a) * MAX_PIXEL_VAL

    return np.asarray(rgb, dtype='uint8')


def geo_ref_raster(
    img_path,
    dst_path,
    src_code=32632,
    dst_code=4326,
    affine_factor=1,
    overwrite=True,
):
    """Georefence raster image.

    Args:
        img_path (str): path to image.
        dst_path (str): destination path.
        src_code (int): source EPSG code.
        dst_code (int): destination EPSG code.
        affine_factor (int): affine transform factor.
        overwrite (bool): whether to overwrite existing file.
    
    Returns:
        str: path to georeferenced image.
    """
    # if already exists, return path
    if os.path.exists(dst_path) and not overwrite:
        print(f'File {dst_path} already exists.')
        return dst_path
    
    # read image
    with warnings.catch_warnings():
        warnings.simplefilter('ignore')
        # Use authenticated environment configuration
        with rasterio.Env(AWS_REGION='us-east-2', CHECK_DISK_FREE_SPACE='FALSE'):
            img = rasterio.open(img_path)
    
    # get image array, crs, and affine transform
    img_array = img.read()
    
    # convert RGBA to RGB
    if img_array.shape[0] == 4:
        img_array = rgba2rgb(img_array)

    # get source crs and affine transform
    crs = CRS.from_epsg(src_code)
    affine_coefs = np.array((0.1, 0.0, 0.0, 0.0, -0.1, 0.0)) * affine_factor
    affine = rasterio.Affine(*affine_coefs)

    # georeference image and write temporary file
    temp_path = tempfile.mktemp(suffix='.tiff', dir=TEMP_DIR)
    with rasterio.Env(AWS_REGION='us-east-2', CHECK_DISK_FREE_SPACE='FALSE'):
        with rasterio.open(
            temp_path,
            'w',
            driver='GTiff',
            tiled=True,
            compress='zlib',
            blockxsize=256,
            blockysize=256,
            height=img_array.shape[1],
            width=img_array.shape[2],
            count=img_array.shape[0],
            dtype=img_array.dtype,
            crs=crs,
            transform=affine,
        ) as src:
            src.write(img_array)

    # reproject image to destination crs and write to file
    # reproject image to destination crs and write to file
    dst_crs = ':'.join(['EPSG', str(dst_code)])
    
    # Create a local temp file for the final output
    local_dst_path = tempfile.mktemp(suffix='.tiff', dir=TEMP_DIR)
    
    
    with rasterio.Env(AWS_NO_SIGN_REQUEST='YES', AWS_REGION='us-east-2', CHECK_DISK_FREE_SPACE='FALSE'):
        with rasterio.open(temp_path) as src:
            transform, width, height = calculate_default_transform(
                src.crs, dst_crs, src.width, src.height, *src.bounds,
                dst_height=src.height, dst_width=src.width,  # very important to keep same size
            )
            kwargs = src.meta.copy()
            kwargs.update(
                {
                    'crs': dst_crs,
                    'transform': transform,
                    'width': width,
                    'height': height,
                    # Optimization: Make it a COG (Tiled + Compressed)
                    'tiled': True,
                    'compress': 'zlib',
                    'blockxsize': 256,
                    'blockysize': 256,
                },
            )
            # Write to LOCAL temp file
            with rasterio.open(local_dst_path, 'w', **kwargs) as dst:
                for i in range(1, src.count + 1):
                    reproject(
                        source=rasterio.band(src, i),
                        destination=rasterio.band(dst, i),
                        src_transform=src.transform,
                        src_crs=src.crs,
                        dst_transform=transform,
                        dst_crs=dst_crs,
                        dst_nodata=MAX_PIXEL_VAL,
                        resampling=Resampling.nearest,
                    )
    
    # Check if the intended destination is S3
    if vio.is_s3(dst_path):
        # Parse bucket and key
        parts = dst_path.replace("s3://", "").split("/", 1)
        bucket = parts[0]
        key = parts[1]
        
        # Upload using boto3 put_object (single part)
        s3_client = boto3.client("s3", region_name='us-east-2', config=Config(signature_version=UNSIGNED))
        with open(local_dst_path, 'rb') as f:
            s3_client.put_object(Bucket=bucket, Key=key, Body=f)
            
            
        # Remove the local temp file
        os.remove(local_dst_path)
        
        # Invalidate s3fs cache so it sees the new file
        if hasattr(vio, 'fs'):
            vio.fs.invalidate_cache(dst_path)
    else:
        # Move the local file to the final destination
        import shutil
        shutil.move(local_dst_path, dst_path)

    # remove temporary file
    os.remove(temp_path)
    return dst_path


def geo_raster_to_meshgrid(georef_img_path):
    """Convert georeferenced raster image to meshgrid.
    
    Args:
        georef_img_path (str): path to georeferenced image.
    
    Returns:
        tuple: tuple of meshgrid.
    """
    # read geo-referenced image and get number of digits
    geo_ref_img = rasterio.open(georef_img_path)

    # get bounds of lontitude and latitude
    lon_min, lat_max, lon_max, lat_min = geo_ref_img.bounds

    # make meshgrid
    xs = np.linspace(lon_min, lon_max, geo_ref_img.width, dtype=np.float64)
    ys = np.linspace(lat_min, lat_max, geo_ref_img.height, dtype=np.float64)
    xx, yy = np.meshgrid(xs, ys)
    
    return xx, yy


def index_to_meshgrid_coord(index, meshgrid):
    """Convert index to meshgrid coordinates.
    
    Args:
        index (np.ndarray): index array of shape (n, 2).
        meshgrid (tuple): tuple of xx and yy in meshgrid.
    
    Returns:
        tuple: tuple of x and y coordinates.
    """
    # ravel 2d meshgrid to 1d
    xx, yy = meshgrid
    y_max, x_max = xx.shape
    xx1d = xx.ravel()
    yy1d = yy.ravel()
    
    # ravel 2d index to 1d
    index1d = np.ravel_multi_index(index.T, (y_max, x_max))
    coord_x = xx1d[index1d]
    coord_y = yy1d[index1d]
    return coord_x, coord_y
