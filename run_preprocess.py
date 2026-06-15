import argparse
import os
import sys
import uuid
import time
import shutil
import json
import toml

# Ensure we can import niceview
# Assuming this script is run from the project root or niceview is installed
sys.path.append(os.path.dirname(os.path.abspath(__file__)))

try:
    from niceview.interface.interface import get_parameter, dumpjson_parameter_from_user_input, files_generate, get_wsi, get_data_path_cache_path
    # import niceview.utils.io as vio  <-- REMOVED to avoid authenticated logic
    from preprocess import preprocess_image
    from dash_viv_viewer.utils import convert_to_ome_tiff
except ImportError as e:
    print(f"Error importing modules: {e}")
    sys.exit(1)

import boto3
from botocore import UNSIGNED
from botocore.config import Config
from boto3.s3.transfer import TransferConfig

# --- Local Anonymous S3 Helpers (Matching hpc_bot_loop.py) ---

def is_s3(path):
    return str(path).strip().startswith("s3://")

def join_path(*args):
    if any(is_s3(a) for a in args):
        return "/".join([str(a).rstrip('/') for a in args if a])
    return os.path.join(*args)

def _s3_upload_anonymous(local_path, s3_path):
    """Anonymous S3 upload forcing single-part for large files (up to 5GB)."""
    parts = s3_path.replace("s3://", "").split("/", 1)
    bucket = parts[0]
    key = parts[1]
    
    s3_client = boto3.client("s3", region_name='us-east-2', config=Config(signature_version=UNSIGNED))
    # Multipart threshold set to 5GB (max for single part) to allow anon upload of large files
    # Anonymous users cannot initiate multipart uploads.
    config = TransferConfig(multipart_threshold=5 * 1024**3)
    
    print(f"Uploading {local_path} to {s3_path} (Anonymous)...")
    s3_client.upload_file(local_path, Bucket=bucket, Key=key, Config=config)

def _s3_upload_presigned(local_path, url):
    """Upload using a presigned PUT URL."""
    import requests
    print(f"Uploading {local_path} via Presigned URL...")
    with open(local_path, 'rb') as f:
        resp = requests.put(url, data=f)
        if resp.status_code == 200:
            print("Presigned upload successful.")
        else:
            print(f"Presigned upload failed: {resp.status_code} {resp.text}")
            raise Exception("Presigned upload failed")

def _s3_parts(s3_path):
    parts = s3_path.replace("s3://", "").split("/", 1)
    if len(parts) != 2:
        raise ValueError(f"Invalid S3 path: {s3_path}")
    return parts[0], parts[1]

def _s3_read_text(path):
    bucket, key = _s3_parts(path)
    s3_client = boto3.client("s3", region_name='us-east-2', config=Config(signature_version=UNSIGNED))
    print(f"[hpc_io] Reading S3 object without s3fs: {path}")
    obj = s3_client.get_object(Bucket=bucket, Key=key)
    return obj["Body"].read().decode("utf-8")

def _s3_write_text(path, text):
    bucket, key = _s3_parts(path)
    s3_client = boto3.client("s3", region_name='us-east-2')
    print(f"[hpc_io] Writing S3 object without s3fs: {path}")
    s3_client.put_object(Bucket=bucket, Key=key, Body=text.encode("utf-8"))

def copy(src, dst):
    """Local copy helper that handles S3 uploads anonymously OR via presigned URL."""
    src_s3 = is_s3(src)
    dst_s3 = is_s3(dst)
    
    if src_s3:
        # We don't implement S3->Local or S3->S3 in this quick helper unless needed.
        # hpc_bot usually provides local paths for inputs.
        # But if we need S3 download:
        print(f"Warning: S3 source copy not fully implemented in local helper: {src}")
        return
        
    if dst_s3:
        # Check for Presigned Match
        # We rely on GLOBAL 'args' being available or passed. 
        # Ideally we pass it, but for this quick script global is cleaner than refactoring everything.
        # We access 'args' from the global scope (parsed at bottom).
        
        uploaded_via_presigned = False
        if 'args' in globals():
            presigned_candidates = [
                ("overlay", args.presigned_key_overlay, args.presigned_url_overlay),
                ("overlay_file", args.presigned_key_overlay_file, args.presigned_url_overlay_file),
            ]
            for label, expected_key, presigned_url in presigned_candidates:
                if expected_key and presigned_url and str(dst).endswith(expected_key):
                    try:
                        print(f"Using presigned upload for {label}: {expected_key}")
                        _s3_upload_presigned(src, presigned_url)
                        uploaded_via_presigned = True
                        break
                    except Exception as e:
                        print(f"Presigned upload error for {label}: {e}")

        if not uploaded_via_presigned:
            print(f"No presigned URL matched {dst}; falling back to anonymous upload.")
            _s3_upload_anonymous(src, dst)
    else:
        # Local -> Local
        shutil.copy(src, dst)

# Alias for compatibility with existing calls
class VioMock:
    pass
vio = VioMock()
vio.join_path = join_path
vio.copy = copy
vio.is_s3 = is_s3

def load_json(path):
    if is_s3(path):
        return json.loads(_s3_read_text(path))
    with open(path, "r") as f:
        return json.load(f)

def dump_json(obj, path, indent=None):
    text = json.dumps(obj, indent=indent)
    if is_s3(path):
        _s3_write_text(path, text)
    else:
        with open(path, "w") as f:
            f.write(text)

def load_toml(path):
    if is_s3(path):
        return toml.loads(_s3_read_text(path))
    with open(path, "r") as f:
        return toml.load(f)

def dump_toml(obj, path):
    text = toml.dumps(obj)
    if is_s3(path):
        _s3_write_text(path, text)
    else:
        with open(path, "w") as f:
            f.write(text)

def get_data_path_cache_path(work_dir):
    configs = load_toml(f'{work_dir}/user/config.toml')
    data_path = configs['path']['data']
    cache_path = configs['path']['cache']
    return data_path, cache_path

vio.load_json = load_json
vio.dump_json = dump_json
vio.load_toml = load_toml
vio.dump_toml = dump_toml

try:
    import niceview.interface.interface as niceview_interface
    niceview_interface.vio = vio
except Exception as e:
    print(f"[hpc_io] Warning: could not patch niceview interface vio: {e}")


import tempfile

def run_preprocess(work_dir, image_path, sample_id=None, folder_id="", mode="replace", cell_types_json=None):
    """
    Replicates the logic of upload_image from niceview/interface/callback.py
    but designed to be run as a standalone script.
    """
    original_image_path = image_path # Keep track of original for basename

    print(f"Starting preprocess for {image_path} (Mode: {mode})")
    print(f"Work Dir: {work_dir}")
    
    # Create a temporary directory for preprocessing artifacts
    temp_dir = tempfile.mkdtemp()
    print(f"Created temporary directory: {temp_dir}")
    
    try:
        # Run Preprocessing
        try:
            is_overlay_mode = (mode == "overlay")
            # Pass temp_dir as output_dir
            processed_path = preprocess_image(image_path, is_overlay=is_overlay_mode, output_dir=temp_dir)
            print(f"Preprocessed image created at: {processed_path}")
            image_path = processed_path
        except Exception as e:
            print(f"Preprocessing error: {e}")
            raise e

        data_path, cache_path = get_data_path_cache_path(work_dir)
        try:
            thor, args, p_input_json = get_parameter(folder_id, work_dir)
        except Exception as e:
            print(f"Failed to get parameters: {e}")
            raise e

        # Generate or use provided sample_id
        if not sample_id:
            sample_id = args.get('sampleId', str(uuid.uuid4()))
        
        # Ensure sample_id in args is consistent if we are overlaying on existing
        if mode == "overlay":
            sample_id = args.get('sampleId') # Use existing sample ID
            if not sample_id:
                 print("Error: No existing sampleId found for overlay mode.")
                 return
        
        basename = os.path.splitext(os.path.basename(original_image_path))[0]
        
        # Handle optional cell_types_json input
        if cell_types_json and os.path.exists(cell_types_json):
            target_json_name = f"{sample_id}-present-cell-types.json"
            target_json_path = vio.join_path(data_path, target_json_name)
            print(f"Copying cell types JSON {cell_types_json} to {target_json_path}")
            vio.copy(cell_types_json, target_json_path)

        if mode == "replace":
            print(f"Processing data dimensions for {image_path}...")
            try:
                # Use the potentially preprocessed image
                height, width = thor.process_data(sample_id, img_path=image_path)
                print(f"Dimensions: {height}x{width}")
            except Exception as e:
                print(f"Error in process_data: {e}")
                raise e

            # Update dimensions
            args["heightWidth"] = [height, width]
            
            args['sampleId'] = sample_id
            args['fileName'] = basename
            dumpjson_parameter_from_user_input(folder_id, work_dir, args=args)
            
            files = files_generate(sample_id)
            dst_path = vio.join_path(data_path, files["img"])
            print(f"Copying {image_path} to {dst_path}")
            vio.copy(image_path, dst_path)
            print("Copy done.")

            print("Generating WSI tiling...")
            get_wsi(folder_id, work_dir)
            print("WSI tiling complete.")
            
        elif mode == "overlay":
            from niceview.interface.interface import cache_generate
            
            original_filename = args.get("fileName", basename)
            sample_id_file = f"{sample_id}-{original_filename}"
            
            cache = cache_generate(sample_id, sample_id_file=sample_id_file) 

            overlay_ome_path = os.path.join(temp_dir, f"{sample_id_file}-gis-blend-cell-type-img.ome.tiff")
            print("[overlay_ome] Converting Loki overlay to OME-TIFF before S3 cache upload.", flush=True)
            print(f"[overlay_ome] Source overlay: {image_path}", flush=True)
            print(f"[overlay_ome] Converted overlay path: {overlay_ome_path}", flush=True)
            image_path = convert_to_ome_tiff(image_path, overlay_ome_path)
            print(f"[overlay_ome] Conversion complete; cache uploads will use: {image_path}", flush=True)
            
            target_name_file = cache.get("gis-blend-cell-type-file")
            if target_name_file:
                dst_path_file = vio.join_path(cache_path, target_name_file)
                print(f"[overlay_ome] Uploading OME overlay file to {dst_path_file}", flush=True)
                vio.copy(image_path, dst_path_file)
            else:
                 print("Error: Could not resolve cache key 'gis-blend-cell-type-file'")

            print("Overlay copied to cache.")

            # (Full resolution overlay is already saved under original ID)

        print("Preprocess completed successfully.")
        
    finally:
        # Cleanup temp dir
        if os.path.exists(temp_dir):
            print(f"Cleaning up temporary directory: {temp_dir}")
            shutil.rmtree(temp_dir)

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Run preprocessing for Loki/Niceview")
    parser.add_argument("--work_dir", required=True, help="Working directory (S3 or local)")
    parser.add_argument("--image", required=True, help="Path to the image to process")
    parser.add_argument("--sample_id", help="Sample ID to use", default=None)
    parser.add_argument("--folder_id", help="Folder ID", default="")
    parser.add_argument("--mode", help="Mode: replace or overlay", default="replace")
    parser.add_argument("--cell_types_json", help="Path to present_cell_types.json", default=None)
    
    # Presigned Upload Arguments (Optional)
    parser.add_argument("--presigned_url_overlay", help="Presigned PUT URL for overlay", default=None)
    parser.add_argument("--presigned_key_overlay", help="Expected S3 Key for overlay", default=None)
    parser.add_argument("--presigned_url_overlay_file", help="Presigned PUT URL for file-specific overlay", default=None)
    parser.add_argument("--presigned_key_overlay_file", help="Expected S3 Key for file-specific overlay", default=None)

    args = parser.parse_args()
    
    # args is global, so copy() can see it
    run_preprocess(args.work_dir, args.image, args.sample_id, args.folder_id, args.mode, args.cell_types_json)
