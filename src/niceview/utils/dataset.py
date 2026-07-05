"""Dataset utilities."""

import os
import time
import requests
from types import SimpleNamespace
import niceview.utils.io as vio
from niceview.utils.aristotle import AristotleDataset
TUTORIAL_IMAGE_S3_PATH = "s3://alextrywebsite/tutorial/loki_tutorial_hskin_melanoma_downsampled.ome.tif"
TUTORIAL_SAMPLE_ID_FILE = "copilot-tutorial-file-name"

class ThorQuery:
    """Container for query."""
    
    def __init__(
        self,
        data_path,
        cache_path, 
        data_extension, 
        cache_extension, 
        cell_label_encoder, 
        cell_label_cmap, 
        primary_key_list,
    ):
        """Initialize query.
        
        Args:
            data_path (str): data path.
            cache_path (str): cache path.
            data_extension (dict): data extension.
            cache_extension (dict): cache extension.
            cell_label_encoder (dict): cell label encoder.
            cell_label_cmap (dict): cell label colormap.
            primary_key_list (list of str): list of primary keys.
        """
        self._data_path = data_path
        self._cache_path = cache_path
        self._data_extension = data_extension
        self._cache_extension = cache_extension
        self._cell_label_encoder = cell_label_encoder
        self._cell_label_cmap = cell_label_cmap
        self._primary_key_list = primary_key_list
    
        self.dataset = AristotleDataset(
            data_path,
            data_extension,
            cache_path,
            cache_extension,
            primary_key_list,
        )

    def wsi_gis(self, sample_id, local_img_path=None):
        """WSI GIS: convert WSI image to pyramidal OME-TIFF for VivViewer.

        Args:
            sample_id (str): sample id.
            local_img_path (str, optional): Path to local image to avoid S3 download.
        """
        from dash_viv_viewer import convert_to_ome_tiff as _convert_to_ome_tiff
        import tempfile

        src_path = self.dataset.get_data_field(sample_id, 'wsi-img')
        dst_path = self.dataset.get_cache_field(sample_id, 'gis-wsi-img')

        if vio.exists(dst_path):
            print(f"[wsi_gis] OME-TIFF already exists, but forcing overwrite: {dst_path}")
            # return

        print(f"[wsi_gis] Converting {src_path} → {dst_path}")

        if local_img_path and os.path.exists(local_img_path) and not str(local_img_path).startswith('s3://'):
            print(f"[wsi_gis] Using provided local image to skip S3 download: {local_img_path}")
            if dst_path.startswith('s3://'):
                with tempfile.TemporaryDirectory(dir=vio.TEMP_DIR) as tmpdir:
                    local_dst = os.path.join(tmpdir, 'wsi_out.ome.tif')
                    _convert_to_ome_tiff(local_img_path, output_path=local_dst)
                    print(f"[wsi_gis] Uploading OME-TIFF to: {dst_path}")
                    vio._s3_upload_single_part(local_dst, dst_path)
                    vio.fs.invalidate_cache(dst_path)
            else:
                _convert_to_ome_tiff(local_img_path, output_path=dst_path)
        elif src_path.startswith('s3://'):
            # Download → convert locally → upload back to S3
            with tempfile.TemporaryDirectory(dir=vio.TEMP_DIR) as tmpdir:
                local_src = os.path.join(tmpdir, 'wsi_src.tiff')
                local_dst = os.path.join(tmpdir, 'wsi_out.ome.tif')

                print(f"[wsi_gis] Downloading from S3: {src_path}")
                vio.fs.get(src_path, local_src)

                _convert_to_ome_tiff(local_src, output_path=local_dst)

                print(f"[wsi_gis] Uploading OME-TIFF to: {dst_path}")
                vio._s3_upload_single_part(local_dst, dst_path)
                vio.fs.invalidate_cache(dst_path)
        else:
            # Local path — convert directly
            _convert_to_ome_tiff(src_path, output_path=dst_path)

    def gis_client_and_layer(self, sample_id, cache_field, server_host=None, server_port=None):
        """Return a lightweight client-like object with .filename pointing to the OME-TIFF path.

        Args:
            sample_id (str): sample id.
            cache_field (str): cache field.
            server_host (str, optional): Unused, kept for API compatibility.
            server_port (int, optional): Unused, kept for API compatibility.

        Returns:
            client (SimpleNamespace): Object with .filename attribute.
            layer: None (unused by VivViewer).
        """
        tutorial_mock_fields = {'gis-wsi-img', 'gis-blend-cell-type-img'}
        if sample_id == TUTORIAL_SAMPLE_ID_FILE and cache_field in tutorial_mock_fields:
            file_path = TUTORIAL_IMAGE_S3_PATH
        else:
            file_path = self.dataset.get_cache_field(sample_id, cache_field)

        start_time = time.time()
        file_found = False

        print(f"[gis_client_and_layer] sample_id={sample_id!r}, cache_field={cache_field!r}")
        print(f"[gis_client_and_layer] Resolved file_path: {file_path}")
        while time.time() - start_time < 120:
            if file_path.startswith('s3://'):
                s3_path_no_prefix = file_path[5:]
                bucket, key = s3_path_no_prefix.split('/', 1)
                http_url = f'https://{bucket}.s3.us-east-2.amazonaws.com/{key}'
                try:
                    resp = requests.head(http_url, timeout=5)
                    print(f"[gis_client_and_layer] HEAD {http_url} -> {resp.status_code}")
                    if resp.status_code == 200:
                        file_found = True
                        break
                except Exception as e:
                    print(f"[gis_client_and_layer] Error checking S3 file via HTTP: {e}")
            else:
                found = vio.exists(file_path)
                print(f"[gis_client_and_layer] vio.exists({file_path}) -> {found}")
                if found:
                    file_found = True
                    break

            time.sleep(1)

        if not file_found:
            print(f"[gis_client_and_layer] WARNING: File not found after 120s: {file_path}")
            return None, None

        # Return a lightweight object – VivViewer only needs .filename to build the proxy URL
        client = SimpleNamespace(filename=file_path)
        return client, None

    def process_data(self, sample_id,  height_width=None, img_path=None, mask_path=None, adata_cell_path=None, adata_spot_path=None):
        if img_path is not None:
            # Ultra-fast dimensions check without loading into memory
            if not str(img_path).startswith("s3://") and os.path.exists(img_path):
                import pyvips
                img = pyvips.Image.new_from_file(img_path)
                return img.height, img.width
            else:
                img = vio.open_image(img_path)
                # DO NOT CALL img.load() which forces the entire image to be decoded into RAM!
                width, height = img.size
                return height, width
        return None
