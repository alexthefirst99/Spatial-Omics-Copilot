import os
import json

TEMP_DIR = None  # Use system default temp dir
import toml
import shutil
import cv2
import numpy as np
import pandas as pd
from scipy.sparse import load_npz as scipy_load_npz, save_npz as scipy_save_npz
from PIL import Image
import rasterio
import io


def is_s3(path):
    return False


def ensure_dir(path):
    os.makedirs(path, exist_ok=True)


def open_file(path, mode='r'):
    return open(path, mode)


def exists(path):
    path_str = str(path)
    # Force cache miss for VivViewer-generated pyramidal OME-TIFFs so they are always regenerated.
    if ('/db/cache/' in path_str) and (path_str.endswith('.ome.tiff') or path_str.endswith('.ome.tif')):
        print(f"[file_io] Forcing cache miss for: {path}")
        return False
    return os.path.exists(path)


def remove(path):
    if os.path.exists(path):
        os.remove(path)


def rmdir(path):
    if os.path.exists(path):
        shutil.rmtree(path)


def copy(src, dst):
    shutil.copy(src, dst)


def load_json(path):
    with open(path, 'r') as f:
        return json.load(f)


def dump_json(obj, path, indent=None):
    with open(path, 'w') as f:
        json.dump(obj, f, indent=indent)


def load_toml(path):
    with open(path, 'r') as f:
        return toml.load(f)


def dump_toml(obj, path):
    with open(path, 'w') as f:
        toml.dump(obj, f)


def join_path(*args):
    return os.path.join(*args)


# --- Advanced Wrappers ---

def read_csv(path, **kwargs):
    return pd.read_csv(path, **kwargs)


def write_image(path, img, params=None):
    cv2.imwrite(path, img, params)


def load_npz(path):
    return scipy_load_npz(path)


def save_npy(path, arr, **kwargs):
    np.save(path, arr, **kwargs)


def load_npy(path, **kwargs):
    return np.load(path, **kwargs)


def read_h5ad(path):
    import scanpy as sc
    return sc.read_h5ad(path)


def save_pil_image(img, path, **kwargs):
    img.save(path, **kwargs)


def open_raster(path, mode='r', **kwargs):
    return rasterio.open(path, mode, **kwargs)


def open_image(path):
    return Image.open(path)


def read_image_cv2(path, flags=cv2.IMREAD_COLOR):
    return cv2.imread(path, flags)


def save_npz(path, matrix):
    scipy_save_npz(path, matrix)


def write_list_to_txt(lst, path):
    with open(path, 'w') as f:
        for item in lst:
            f.write(f"{item}\n")


def write_csv(df, path, **kwargs):
    df.to_csv(path, **kwargs)


def zip_folder(folder_path, output_path):
    import zipfile
    with zipfile.ZipFile(output_path, 'w', zipfile.ZIP_DEFLATED) as zipf:
        for root, dirs, files in os.walk(folder_path):
            for file in files:
                file_path = os.path.join(root, file)
                arcname = os.path.relpath(file_path, folder_path)
                zipf.write(file_path, arcname)


# Keep old name as alias for compatibility
zip_folder_s3 = zip_folder
