"""Dataset utilities."""

import os
import time
import requests
import cv2
import rasterio
import pandas as pd
import numpy as np
import scanpy as sc
import PIL
from PIL import Image
from scipy.sparse import load_npz
from types import SimpleNamespace
import niceview.utils.io as vio
from niceview.utils.tools import txt_to_list, select_col_from_name, normalize_array
from niceview.utils.tools import mask_filter_relabel, mask_to_image, mask_to_image_discrete, discrete_cmap_from_hex
from niceview.utils.tools import blend, draw_circles

from niceview.utils.cell import get_nuclei_pixels
from niceview.pyplot.heatmap import heatmap_from_scatter
from niceview.utils.aristotle import AristotleDataset
from scipy import ndimage
import scipy
from pyproj import Geod
import matplotlib.pyplot as plt

Image.MAX_IMAGE_PIXELS = None
CMAX = 255
CMIN = 1  # avoid zero to distinguish from background
TUTORIAL_IMAGE_S3_PATH = "s3://alextrywebsite/tutorial/loki_tutorial_hskin_melanoma_downsampled.ome.tif"
TUTORIAL_SAMPLE_ID_FILE = "loki-tutorial-file-name"


TYPE_NUCLEI_DICT_LOKI = {
    1: "Neoplastic",
    2: "Immune",
    3: "Stromal",
    4: "Epithelial",
    5: "Fibroblast",
    6: "Endothelial",
    7: "Cardiomyocyte",
    8: "Cardiac Fibroblast",
    9: "Smooth Muscle",
    10: "Adipose",
    11: "Oligodendrocyte",
    12: "Astrocyte",
    13: "Neuron",
    14: "Vascular Smooth Muscle",
    15: "Alveolar pneumocytes",
    16: "Chondrocytes",
    17: "Hepatocyte",
    18: "Glia",
    19: "Pericentral hepatocytes",
    20: "Proliferating keratinocytes",
    21: "Spinous keratinocytes",
    22: "Connective",
    23: "Lamina propria",
}

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

    def plot_cell_detection(self, sample_id):
        """Plot cell detection.
            
        Args:
            sample_id (str): sample id.
        """
        # random color for cell mask check
        if not vio.exists(self.dataset.get_cache_field(sample_id, 'mask-cell-random-img')):
#            print("plot cell detection".upper())

            cell_matched_region = get_nuclei_pixels(
                    vio.load_npz(
                        self.dataset.get_data_field(sample_id, 'cell-mask'),
                    ).tocsr()[:, :].todense(),
            )
            vio.write_image(
                self.dataset.get_cache_field(sample_id, 'mask-cell-random-img'),
                mask_to_image(
                    mask_filter_relabel(
                        self.dataset.get_data_field(sample_id, 'cell-mask'),
                        cell_matched_region,
                        np.random.randint(CMIN, CMAX, len(cell_matched_region)),
                    ),
                    cv2.COLORMAP_JET,
                ),
            )

    def cell_analysis(self, sample_id, max_dim, selected_cell_gene_name=None, label_analysis=False, heatmap=False, cell_selection=False, selected_pathway=None, cmap="coolwarm", vmin=None, vmax=None):
        """Cell gene analysis.
        
        Args:
            sample_id (str): sample id.
            selected_cell_gene_name (str): list of selected cell gene name.
            label_analysis (bool): whether to label the cell.
            heatmap (bool): whether to generate heatmap.
            selected_pathway (str): pathway name.
        """

        # cell_info is rescaled cell coordinates
        # cell-mask is also rescaled
        # There is a potential problem here for getting the mask-cell-match-region because of the error caused by rescaling

        cell_info = vio.read_csv(
            self.dataset.get_data_field(sample_id, 'cell-info'),
        )
        cell_pos = cell_info[['x', 'y']].values

#        print(f"cmap is {cmap}")


        if vio.exists(self.dataset.get_cache_field(sample_id, 'mask-cell-match-region')):
            cell_matched_region = vio.load_npy(
                self.dataset.get_cache_field(sample_id, 'mask-cell-match-region'),
                allow_pickle=True,
            )
        else:
            tol = 1e-3

#            print(f"seg label is: {cell_info['seg_label'].values}")
            cell_matched_region = get_nuclei_pixels(
                vio.load_npz(
                    self.dataset.get_data_field(sample_id, 'cell-mask'),
                ).tocsr()[:, :].todense(),
                ad_cell_pos=cell_pos, 
                tol=tol,
                ad_cell_label=cell_info['seg_label'].values,
            )

            vio.save_npy(
                self.dataset.get_cache_field(sample_id, 'mask-cell-match-region'),
                cell_matched_region,
                allow_pickle=True,
            )
        
        # gene
        if selected_cell_gene_name:
            cell_gene = vio.load_npz(
                self.dataset.get_data_field(sample_id, 'cell-gene'),
            )
            cell_gene_name = txt_to_list(
                self.dataset.get_data_field(sample_id, 'cell-gene-name'),
            )
            cell_selected_gene = select_col_from_name(
                cell_gene, cell_gene_name, selected_cell_gene_name,
            )
            cell_selected_gene_norm = normalize_array(cell_selected_gene, CMIN, CMAX, vmin, vmax)
            if not vio.exists(self.dataset.get_cache_field(sample_id, 'mask-cell-gene-img')):
                vio.write_image(
                    self.dataset.get_cache_field(sample_id, 'mask-cell-gene-img'),
                    mask_to_image(
                        mask_filter_relabel(
                            self.dataset.get_data_field(sample_id, 'cell-mask'),
                            cell_matched_region,
                            cell_selected_gene_norm,
                        ),
#                        cv2.COLORMAP_JET,
                        cmap,
                    ),
                )
        

        # label
        # for cnv and similar cell search, we need to change the label to the corresponding label
        if label_analysis:
            cell_label = [
                self._cell_label_encoder[x] for x in cell_info['label'].values
            ]
            if not vio.exists(self.dataset.get_cache_field(sample_id, 'mask-cell-type-img')):
                vio.write_image(
                    self.dataset.get_cache_field(sample_id, 'mask-cell-type-img'),
                    mask_to_image_discrete(
                        mask_filter_relabel(
                            self.dataset.get_data_field(sample_id, 'cell-mask'),
                            cell_matched_region,
                            cell_label,
                        ),
                        discrete_cmap_from_hex(self._cell_label_cmap),
                    ),
                )
        
        # heatmap
        if heatmap:
            cell_gene = vio.load_npz(
                self.dataset.get_data_field(sample_id, 'cell-gene'),
            )
            cell_gene_name = txt_to_list(
                self.dataset.get_data_field(sample_id, 'cell-gene-name'),
            )
            cell_selected_gene = select_col_from_name(
                cell_gene, cell_gene_name, selected_cell_gene_name,
            )
            cell_selected_gene_norm = normalize_array(cell_selected_gene, CMIN, CMAX)
            cell_gene_color = np.array(cell_selected_gene_norm).ravel()
            if not vio.exists(self.dataset.get_cache_field(sample_id, 'cell-gene-heatmap-img')):
                image = vio.open_image(self.dataset.get_data_field(sample_id, 'wsi-img'))
                xmax, ymax = image.size
                heatmap_from_scatter(
                    (xmax, ymax), np.round(cell_pos).astype(int), cell_gene_color, 
                    dst_path=self.dataset.get_cache_field(sample_id, 'cell-gene-heatmap-img'),
                )

        if selected_pathway:
            cell_pathway_matrix = vio.load_npy(self.dataset.get_data_field(sample_id, 'cell-pathway-matrix'), allow_pickle=True)
            cell_pathway_name = txt_to_list(self.dataset.get_data_field(sample_id, 'cell-pathway-name'))
            cell_selected_pathway = select_col_from_name(cell_pathway_matrix, cell_pathway_name, selected_pathway)
            cell_selected_pathway_norm = normalize_array(cell_selected_pathway, CMIN, CMAX)
            cell_pathway_color = np.array(cell_selected_pathway_norm).ravel()
            if not vio.exists(self.dataset.get_cache_field(sample_id, 'cell-pathway-heatmap-img')):
                image = vio.open_image(self.dataset.get_data_field(sample_id, 'wsi-img'))
                xmax, ymax = image.size
                heatmap_from_scatter(
                    (xmax, ymax), np.round(cell_pos).astype(int), cell_pathway_color, 
                    dst_path=self.dataset.get_cache_field(sample_id, 'cell-pathway-heatmap-img'), cmap=cmap
                )

        if cell_selection:
            cell_label = [
                self._cell_label_encoder[x] for x in cell_info['cell_select'].values.astype(str)
            ]
            vio.write_image(
                self.dataset.get_cache_field(sample_id, 'mask-cell-select-img'),
                mask_to_image_discrete(
                    mask_filter_relabel(
                        self.dataset.get_data_field(sample_id, 'cell-mask'),
                        cell_matched_region,
                        cell_label,
                    ),
                    discrete_cmap_from_hex(self._cell_label_cmap),
                ),
            )
    
    def cell_blend(self, sample_id, max_dim, selected_cell_gene_name=None, label_analysis=False,heatmap_analysis=False, cell_selection=False, selected_pathway=None, mask_opacity=1, cmap="coolwarm", vmin=None, vmax=None):
        """Cell blend.

        Args:
            sample_id (str): sample id.
            selected_cell_gene_name (str): list of selected cell gene name.
            label_analysis (bool): whether to label the cell.
            heatmap_analysis (bool): whether to generate heatmap.
            selected_pathway (str): pathway name.
            mask_opacity (float): mask opacity.
        """
        # plot cell detection first as it does not depend on the cell information
        # If the mask-cell-random-img does not exist, then create it
        # If the mask-cell-random-img does exist, then do nothing
        self.plot_cell_detection(sample_id)

         # random color
        if not vio.exists(self.dataset.get_cache_field(sample_id, 'blend-cell-random-img')):
            #print("HEY IM plotting random color")
            vio.write_image(
                self.dataset.get_cache_field(sample_id, 'blend-cell-random-img'),
                blend(
                    self.dataset.get_data_field(sample_id, 'wsi-img'),
                    self.dataset.get_cache_field(sample_id, 'mask-cell-random-img'),
                    mask_opacity,
                ),
            )

        try:
            self.cell_analysis(sample_id, max_dim, selected_cell_gene_name, label_analysis, heatmap_analysis, cell_selection, selected_pathway, cmap, vmin, vmax)
        except FileNotFoundError:
            return

        if selected_cell_gene_name:
            #print("HEY IM plotting gene expression color")
            if not vio.exists(self.dataset.get_cache_field(sample_id, 'blend-cell-gene-img')):
                vio.write_image(
                    self.dataset.get_cache_field(sample_id, 'blend-cell-gene-img'),
                    blend(
                        self.dataset.get_data_field(sample_id, 'wsi-img'),
                        self.dataset.get_cache_field(sample_id, 'mask-cell-gene-img'),
                        mask_opacity,
                    ),
                )
        
        if label_analysis:
            if not vio.exists(self.dataset.get_cache_field(sample_id, 'blend-cell-type-img')):
                vio.write_image(
                    self.dataset.get_cache_field(sample_id, 'blend-cell-type-img'),
                    blend(
                        self.dataset.get_data_field(sample_id, 'wsi-img'),
                        self.dataset.get_cache_field(sample_id, 'mask-cell-type-img'),
                        mask_opacity,
                    ),
                )

        if heatmap_analysis:
            if not vio.exists(self.dataset.get_cache_field(sample_id, 'blend-cell-gene-heatmap-img')):
                vio.write_image(
                    self.dataset.get_cache_field(sample_id, 'blend-cell-gene-heatmap-img'),
                    blend(
                        self.dataset.get_data_field(sample_id, 'wsi-img'),
                        self.dataset.get_cache_field(sample_id, 'cell-gene-heatmap-img'),
                        0.5,
                    ),
                )
        
        if selected_pathway:
            if not vio.exists(self.dataset.get_cache_field(sample_id, 'blend-cell-pathway-heatmap-img')):
                vio.write_image(
                    self.dataset.get_cache_field(sample_id, 'blend-cell-pathway-heatmap-img'),
                    blend(
                        self.dataset.get_data_field(sample_id, 'wsi-img'),
                        self.dataset.get_cache_field(sample_id, 'cell-pathway-heatmap-img'),
                        0.3,
                        heatmap=True
                    ),
                )
        
        if cell_selection:
            vio.write_image(
                self.dataset.get_cache_field(sample_id, 'blend-cell-select-img'),
                blend(
                    self.dataset.get_data_field(sample_id, 'wsi-img'),
                    self.dataset.get_cache_field(sample_id, 'mask-cell-select-img'),
                    mask_opacity,
                ),
            )

    def cell_gis(self, sample_id, max_dim, selected_cell_gene_name=None, label_analysis=False, heatmap_analysis=False, cell_selection=False, selected_pathway=None, mask_opacity=1, cmap="coolwarm", vmin=None, vmax=None):
        """Cell GIS: create OME-TIFF overlays for VivViewer.

        Args:
            sample_id (str): sample id.
            selected_cell_gene_name (str): list of selected cell gene name.
            label_analysis (bool): whether to label the cell.
            heatmap_analysis (bool): whether to generate heatmap.
            selected_pathway (str): pathway name.
            mask_opacity (float): mask opacity.
        """

        # blend
        self.cell_blend(sample_id, max_dim, selected_cell_gene_name, label_analysis, heatmap_analysis, cell_selection, selected_pathway, mask_opacity, cmap, vmin, vmax)
        
        # random color
        if not vio.exists(self.dataset.get_cache_field(sample_id, 'gis-blend-cell-random-img')):
            self._convert_to_gis_tiff(
                self.dataset.get_cache_field(sample_id, 'blend-cell-random-img'),
                self.dataset.get_cache_field(sample_id, 'gis-blend-cell-random-img'),
            )

        # convert OME-TIFFs for blended cell selected gene and cell type
        if selected_cell_gene_name:
            if not vio.exists(self.dataset.get_cache_field(sample_id, 'gis-blend-cell-gene-img')):
                self._convert_to_gis_tiff(
                    self.dataset.get_cache_field(sample_id, 'blend-cell-gene-img'),
                    self.dataset.get_cache_field(sample_id, 'gis-blend-cell-gene-img'),
                )
        
        if label_analysis:
            if not vio.exists(self.dataset.get_cache_field(sample_id, 'gis-blend-cell-type-img')):
                self._convert_to_gis_tiff(
                    self.dataset.get_cache_field(sample_id, 'blend-cell-type-img'),
                    self.dataset.get_cache_field(sample_id, 'gis-blend-cell-type-img'),
                )
        if heatmap_analysis:
            if not vio.exists(self.dataset.get_cache_field(sample_id, 'gis-blend-cell-gene-heatmap-img')):
                self._convert_to_gis_tiff(
                    self.dataset.get_cache_field(sample_id, 'blend-cell-gene-heatmap-img'),
                    self.dataset.get_cache_field(sample_id, 'gis-blend-cell-gene-heatmap-img'),
                )
        if selected_pathway:
            if not vio.exists(self.dataset.get_cache_field(sample_id, 'gis-blend-cell-pathway-heatmap-img')):
                self._convert_to_gis_tiff(
                    self.dataset.get_cache_field(sample_id, 'blend-cell-pathway-heatmap-img'),
                    self.dataset.get_cache_field(sample_id, 'gis-blend-cell-pathway-heatmap-img'),
                )
        if cell_selection:
            self._convert_to_gis_tiff(
                self.dataset.get_cache_field(sample_id, 'blend-cell-select-img'),
                self.dataset.get_cache_field(sample_id, 'gis-blend-cell-select-img'),
            )


    def spot_analysis(self, sample_id, selected_spot_gene_name, thickness=-1, vmin=None, vmax=None):
        """Spot analysis.
        
        Args:
            sample_id (str): sample id
            selected_spot_gene_name (str): list of selected spot gene name.
            thickness (int): thickness of the circle.
        """
        # read image shape
        #img_shape = load_npz(self.dataset.get_data_field(sample_id, 'cell-mask')).shape
        img_shape = vio.read_image_cv2(self.dataset.get_data_field(sample_id, 'wsi-img')).shape[:2]
        
        if selected_spot_gene_name:
            # spot info
            spot_info = pd.read_csv(self.dataset.get_data_field(sample_id, 'spot-info'))
            spot_pos = spot_info[['x', 'y']].values
            spot_diameter = spot_info['diameter'].values
            spot_gene = load_npz(self.dataset.get_data_field(sample_id, 'spot-gene'))
            spot_gene_name = txt_to_list(self.dataset.get_data_field(sample_id, 'spot-gene-name'))
            spot_selected_gene = select_col_from_name(
                spot_gene, spot_gene_name, selected_spot_gene_name,
            )
            spot_selected_gene_norm = normalize_array(spot_selected_gene, CMIN, CMAX, vmin, vmax)
            
            # draw circles
            if not vio.exists(self.dataset.get_cache_field(sample_id, 'circle-spot-gene-img')):
                vio.write_image(
                    self.dataset.get_cache_field(sample_id, 'circle-spot-gene-img'),
                    draw_circles(
                        img_shape,
                        spot_pos,
                        spot_diameter,
                        spot_selected_gene_norm,
                        cmap=cv2.COLORMAP_JET,
                        thickness=-1,
                    ),
                )
    
    def spot_blend(self, sample_id, selected_spot_gene_name, thickness=-1, mask_opacity=1, vmin=None, vmax=None):
        """Spot blend.
        
        Args:
            sample_id (str): sample id.
            selected_spot_gene_name (str): list of selected spot gene name.
            thickness (int): thickness of the circle.
            mask_opacity (float): mask opacity.
        """
        # analysis
        self.spot_analysis(sample_id, selected_spot_gene_name, thickness, vmin, vmax)
        
        if selected_spot_gene_name:
            if not vio.exists(self.dataset.get_cache_field(sample_id, 'blend-spot-gene-img')):
                vio.write_image(
                    self.dataset.get_cache_field(sample_id, 'blend-spot-gene-img'),
                    blend(
                        self.dataset.get_data_field(sample_id, 'wsi-img'),
                        self.dataset.get_cache_field(sample_id, 'circle-spot-gene-img'),
                        mask_opacity,
                    ),
                )
    
    def spot_gis(self, sample_id, selected_spot_gene_name, thickness=-1, mask_opacity=1, vmin=None, vmax=None):
        """Spot GIS: create OME-TIFF overlay for VivViewer.
        
        Args:
            sample_id (str): sample id.
            selected_spot_gene_name (str): list of selected spot gene name.
            thickness (int): thickness of the circle.
            mask_opacity (float): mask opacity.
        """
        # blend
        self.spot_blend(sample_id, selected_spot_gene_name, thickness, mask_opacity, vmin, vmax)
        
        # convert to OME-TIFF for VivViewer
        if selected_spot_gene_name:
            if not vio.exists(self.dataset.get_cache_field(sample_id, 'gis-blend-spot-gene-img')):
                self._convert_to_gis_tiff(
                    self.dataset.get_cache_field(sample_id, 'blend-spot-gene-img'),
                    self.dataset.get_cache_field(sample_id, 'gis-blend-spot-gene-img'),
                )

    def _convert_to_gis_tiff(self, src_path, dst_path):
        """Convert a PNG/TIFF blend image to a pyramidal OME-TIFF for VivViewer.

        Handles both local and S3 paths by downloading to a temp file if needed.

        Args:
            src_path (str): Source image path (local or s3://).
            dst_path (str): Destination OME-TIFF path (local or s3://).
        """
        from dash_viv_viewer import convert_to_ome_tiff as _convert_to_ome_tiff
        import tempfile

        print(f"[_convert_to_gis_tiff] {src_path} → {dst_path}")

        src_is_s3 = src_path.startswith('s3://')
        dst_is_s3 = dst_path.startswith('s3://')

        if src_is_s3 or dst_is_s3:
            with tempfile.TemporaryDirectory(dir=vio.TEMP_DIR) as tmpdir:
                local_src = os.path.join(tmpdir, 'src_blend.png') if src_is_s3 else src_path
                local_dst = os.path.join(tmpdir, 'dst_gis.ome.tif')

                if src_is_s3:
                    vio.fs.get(src_path, local_src)

                _convert_to_ome_tiff(local_src, output_path=local_dst)

                if dst_is_s3:
                    vio._s3_upload_single_part(local_dst, dst_path)
                    vio.fs.invalidate_cache(dst_path)
                else:
                    import shutil
                    shutil.move(local_dst, dst_path)
        else:
            _convert_to_ome_tiff(src_path, output_path=dst_path)
    
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

    def empty_cache(self, sample_id, cache_field):
        """Empty cache.
        
        Args:
            sample_id (str): sample id.
            cache_field (str): cache field.
        """
        vio.remove(self.dataset.get_cache_field(sample_id, cache_field))

    def empty_cache_cell(self, sample_id, gene=False, label=False, heatmap=False, pathway=False):
        """Empty cell gene.
        
        Args:
            sample_id (str): sample id.
            gene (bool): whether to empty cell gene.
            label (bool): whether to empty cell label.
        """
        try:
            if gene:
                self.empty_cache(sample_id, 'mask-cell-gene-img')
                self.empty_cache(sample_id, 'blend-cell-gene-img')
                self.empty_cache(sample_id, 'gis-blend-cell-gene-img')

            if label:
                self.empty_cache(sample_id, 'mask-cell-type-img')
                self.empty_cache(sample_id, 'blend-cell-type-img')
                self.empty_cache(sample_id, 'gis-blend-cell-type-img')

            if heatmap:
                self.empty_cache(sample_id, 'cell-gene-heatmap-img')
                self.empty_cache(sample_id, 'blend-cell-gene-heatmap-img')
                self.empty_cache(sample_id, 'gis-blend-cell-gene-heatmap-img')

            if pathway:
                self.empty_cache(sample_id, 'cell-pathway-heatmap-img')
                self.empty_cache(sample_id, 'blend-cell-pathway-heatmap-img')
                self.empty_cache(sample_id, 'gis-blend-cell-pathway-heatmap-img')
                
        except FileNotFoundError:
            pass
    
    def empty_cache_spot(self, sample_id, gene=False):
        """Empty spot gene.
        
        Args:
            sample_id (str): sample id.
            gene (bool): whether to empty spot gene.
        """
        try:
            if gene:
                self.empty_cache(sample_id, 'circle-spot-gene-img')
                self.empty_cache(sample_id, 'blend-spot-gene-img')
                self.empty_cache(sample_id, 'gis-blend-spot-gene-img')

        except FileNotFoundError:
            pass

    # def gis_client_and_layer(self, sample_id, cache_field):
    #     """GIS client.
        
    #     Args:
    #         sample_id (str): sample id.
    #         cache_field (str): cache field.
        
    #     Returns:
    #         client (TileClient): tile client.
    #         layer (TileLayer): tile layer.
    #     """
    #     client = TileClient(
    #         self.dataset.get_cache_field(sample_id, cache_field),
    #         cors_all=True,
    #         host="0.0.0.0",
    #         #port=1999,
    #         #client_host="3.23.18.185",
    #         #client_port=1999,
    #     )
    #     layer = get_leaflet_tile_layer(client)
    #     return client, layer



    def get_unique_cell_types(self, sample_id):
        """Get unique cell types.

        Args:
            sample_id (str): sample id.

        Returns:
            dict: {id: name} of unique cell types.
        """
        # 1. Try to read from present_cell_types.json (Loki Output)
        # This file might be in the cache directory or data directory depending on how we saved it.
        # run_preprocess.py (as we will modify it) should save it.
        # Let's assume it is saved as {sample_id}-present-cell-types.json in DATA dir
        
        json_filename = f"{sample_id}-present-cell-types.json"
        json_path = vio.join_path(self.dataset.data_dir, json_filename)
        
        if vio.exists(json_path):
            try:
                present_types = vio.load_json(json_path) # List of ints
                unique_classes = {}
                for idx in present_types:
                    idx = int(idx)
                    if idx in TYPE_NUCLEI_DICT_LOKI:
                        unique_classes[idx] = TYPE_NUCLEI_DICT_LOKI[idx]
                    else:
                         unique_classes[idx] = f"Type {idx}"
                return unique_classes
            except Exception as e:
                print(f"Error reading {json_path}: {e}")

        # 2. Fallback to cell-info.csv
        cell_info_path = self.dataset.get_data_field(sample_id, 'cell-info')
        if vio.exists(cell_info_path):
            cell_info = vio.read_csv(cell_info_path)
            if 'label' in cell_info.columns:
                unique_labels = cell_info['label'].unique()
                unique_classes = {}
                for label in unique_labels:
                    # If label is already int (Loki style)
                    try:
                        idx = int(label)
                        if idx in TYPE_NUCLEI_DICT_LOKI:
                            unique_classes[idx] = TYPE_NUCLEI_DICT_LOKI[idx]
                        elif label in self._cell_label_encoder:
                             idx = self._cell_label_encoder[label]
                             unique_classes[idx] = label
                        else:
                            unique_classes[idx] = f"Type {idx}"
                    except ValueError:
                         # String label
                        if label in self._cell_label_encoder:
                            idx = self._cell_label_encoder[label]
                            unique_classes[idx] = label
                return unique_classes
        
        return {}

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

    def get_coord_mapping(self, sample_id):
        """Get coordinate mapping from geographic to pixel.
        
        Args:
            sample_id (str): sample id.
        
        Returns:
            func: coordinate mapping function.
        """
        raster = vio.open_raster(
            self.dataset.get_cache_field(sample_id, 'gis-wsi-img'),
        )
        return raster.index

    # Alex added
    # 2/25/2025
    def reverse_coordinate_mapping(self, sample_id):
        """Get coordinate mapping from geographic to pixel.
        
        Args:
            sample_id (str): sample id.
        
        Returns:
            func: coordinate mapping function.
        """
        raster = rasterio.open(
            self.dataset.get_cache_field(sample_id, 'gis-wsi-img'),
        )
        return raster.xy

    def get_gene_max(self, sample_id, selected_cell_gene_name):
        """Get cmax.
        
        Args:
            sample_id (str): sample id.
            selected_cell_gene_name (str): list of selected cell gene name.
        
        Returns:
            float: max value.
        """
        cell_gene = load_npz(
            self.dataset.get_data_field(sample_id, 'cell-gene'),
        )
        cell_gene_name = txt_to_list(
            self.dataset.get_data_field(sample_id, 'cell-gene-name'),
        )
        cell_selected_gene = select_col_from_name(
            cell_gene, cell_gene_name, selected_cell_gene_name,
        )
        return float(cell_selected_gene.max())

    def get_pathway_max(self, sample_id, selected_pathway):
        """Get cmax.
        
        Args:
            sample_id (str): sample id.
            selected_pathway (str): pathway name.
        
        Returns:
            float: max value.
        """
        cell_pathway = np.load(
            self.dataset.get_data_field(sample_id, 'cell-pathway-matrix'), allow_pickle=True
        )
        cell_pathway_name = txt_to_list(
            self.dataset.get_data_field(sample_id, 'cell-pathway-name'),
        )
        cell_selected_pathway = select_col_from_name(
            cell_pathway, cell_pathway_name, selected_pathway,
        )
        return float(cell_selected_pathway.max())

    def get_spot_max(self, sample_id, selected_spot_gene):
        """Get cmax.
        
        Args:
            sample_id (str): sample id.
            selected_pathway (str): pathway name.
        
        Returns:
            float: max value.
        """
        spot_gene = load_npz(
            self.dataset.get_data_field(sample_id, 'spot-gene'),
        )
        spot_gene_name = txt_to_list(
            self.dataset.get_data_field(sample_id, 'spot-gene-name'),
        )
        spot_selected_gene = select_col_from_name(
            spot_gene, spot_gene_name, selected_spot_gene,
        )
        return float(spot_selected_gene.max())

    def get_cell_adata_and_img(self, sample_id, local_img_path=None):
        """Get cell adata.
        
        Args:
            sample_id (str): sample id.
            local_img_path (str, optional): skip S3 download
        
        Returns:
            anndata.AnnData: cell adata.
            numpy.ndarray: image.
        """
        try:
            cell_adata = sc.read_h5ad(
                self.dataset.get_data_field(sample_id, 'cell'),
            )
        except FileNotFoundError:
            cell_adata = None
            pass
        
        if local_img_path and os.path.exists(local_img_path) and not str(local_img_path).startswith("s3://"):
            print(f"[get_cell_adata] Using local image to skip S3 download: {local_img_path}")
            img = cv2.imread(local_img_path)
        else:
            img = vio.read_image_cv2(
                self.dataset.get_data_field(sample_id, 'wsi-img'),
            )
        return cell_adata, img


    def get_factor(self, gis_img_path, actual_distance=1e-6):
        """Get factor for converting pixel distance to actual distance.
        
        Args:
            gis_img_path (str): path to the GIS image.
            actual_distance (float): actual distance in micrometer.
            
        Returns:
            float: factor.
        """
        with rasterio.open(gis_img_path) as src:        
            lat1, lon1 = src.xy(0, 0)
            lat2, lon2 = src.xy(0, 1)
        g = Geod(ellps='clrk66') 
        _, _, dist = g.inv(lon1, lat1, lon2, lat2)
        factor = actual_distance / dist * 10 ** 6  # first convert to meter then convert to micrometer
        return factor

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



