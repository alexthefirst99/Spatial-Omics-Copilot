import anndata as ad
import numpy as np
import scanpy as sc
from scipy import sparse
import re


def vmax_vmin_gene_exp(adata, gene, vmax=None , vmin=None):
    gene_array = adata[:,adata.var.index == gene].X.toarray()
    if vmax is not None:
        if isinstance(vmax, float):
            vmax_q =vmax
            vmax_q = np.clip(gene_array,a_max=vmax_q,a_min=gene_array.min())
            adata[:,adata.var.index ==gene].X = vmax_q
        else:
            vmax = re.search(r'p(\d+)', vmax)
            vmax = int(vmax.group(1))
            vmax = vmax / 100
            vmax_q = np.quantile(gene_array, vmax)
            vmax_q = np.clip(gene_array,a_max=vmax_q,a_min=gene_array.min())
            adata[:,adata.var.index ==gene].X = vmax_q
    elif vmin is not None :
        if isinstance(vmin, float): 
            vmin_q = vmin
            vmin_q = np.clip(gene_array,a_max=gene_array.max(),a_min=vmin_q)
            adata[:,adata.var.index == gene].X = vmin_q
        else:
            vmin = re.search(r'p(\d+)', vmin)
            vmin = int(vmin.group(1))
            vmin = vmin / 100
            vmin_q = np.quantile(gene_array, vmin)
            vmin_q = np.clip(gene_array,a_max=gene_array.max(),a_min=vmin_q)
            adata[:,adata.var.index == gene].X = vmin_q
    elif vmin is not None and vmax is not None:
        if isinstance(vmin, float) and isinstance(vmax, float):
            vmin_q = vmin
            vmax_q =vmax
            min_max_q = np.clip(gene_array,a_max=vmax_q,a_min=vmin_q)
            adata[:,adata.var.index == gene].X = min_max_q
        else:
            vmin = re.search(r'p(\d+)', vmin)
            vmin = int(vmin.group(1))
            vmin = vmin / 100
            vmax = re.search(r'p(\d+)', vmax)
            vmax = int(vmax.group(1))
            vmax = vmax / 100
            vmin_q = np.quantile(gene_array, vmin)
            vmax_q = np.quantile(gene_array, vmax)
            min_max_q = np.clip(gene_array,a_max=vmax_q,a_min=vmin_q)
            adata[:,adata.var.index == gene].X = min_max_q  
    return adata