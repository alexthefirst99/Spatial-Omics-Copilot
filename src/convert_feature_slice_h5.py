from __future__ import annotations

import argparse
import json
from pathlib import Path

import anndata as ad
import h5py
import numpy as np
import pandas as pd
import scipy.sparse as sp


def _decode_h5_strings(values):
    decoded = []
    for value in values:
        if isinstance(value, bytes):
            decoded.append(value.decode("utf-8", errors="replace"))
        else:
            decoded.append(str(value))
    return decoded


def _metadata_json_from_h5(h5_file):
    metadata = h5_file.attrs.get("metadata_json", "{}")
    if isinstance(metadata, bytes):
        metadata = metadata.decode("utf-8", errors="replace")
    return json.loads(metadata)


def _coo_group_to_arrays(group):
    return group["row"][:], group["col"][:], group["data"][:]


def _microscope_spatial_from_bins(bin_rows, bin_cols, metadata, binning_scale):
    raw_cols = (np.asarray(bin_cols, dtype=np.float64) + 0.5) * float(binning_scale)
    raw_rows = (np.asarray(bin_rows, dtype=np.float64) + 0.5) * float(binning_scale)
    transform = (metadata.get("transform_matrices") or {}).get("spot_colrow_to_microscope_colrow")
    if not transform:
        return np.column_stack([raw_cols, raw_rows])

    matrix = np.asarray(transform, dtype=np.float64)
    points = np.column_stack([raw_cols, raw_rows, np.ones_like(raw_cols)])
    mapped = points @ matrix.T
    w = mapped[:, 2]
    safe_w = np.where(np.abs(w) > 1e-12, w, 1.0)
    return np.column_stack([mapped[:, 0] / safe_w, mapped[:, 1] / safe_w])


def _bin_diameter_fullres(metadata, binning_scale):
    transform = (metadata.get("transform_matrices") or {}).get("spot_colrow_to_microscope_colrow")
    if not transform:
        return float(binning_scale)
    matrix = np.asarray(transform, dtype=np.float64)
    x_scale = float(np.linalg.norm(matrix[:2, 0]))
    y_scale = float(np.linalg.norm(matrix[:2, 1]))
    return max(1.0, float(binning_scale) * ((x_scale + y_scale) / 2.0))


def convert_feature_slice_h5_to_h5ad(source_path, output_path, binning_scale=8):
    """Convert a 10x Visium HD feature_slice.h5 file to sparse AnnData h5ad.

    The default binning scale of 8 maps 2 um feature-slice bins to 16 um bins.
    This keeps the converted file practical for the app while retaining spatial
    coordinates in full-resolution microscope pixel space.
    """
    source_path = Path(source_path)
    output_path = Path(output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    binning_scale = int(binning_scale)
    if binning_scale < 1:
        raise ValueError("binning_scale must be >= 1")

    with h5py.File(source_path, "r") as h5_file:
        required = ("feature_slices", "features", "masks", "umis")
        missing = [key for key in required if key not in h5_file]
        if missing:
            raise ValueError(
                "Unsupported .h5 file. Expected a 10x Visium HD feature_slice.h5 "
                f"with groups {required}; missing {missing}."
            )

        metadata = _metadata_json_from_h5(h5_file)
        nrows = int(metadata["nrows"])
        ncols = int(metadata["ncols"])
        nrows_binned = int(np.ceil(nrows / float(binning_scale)))
        ncols_binned = int(np.ceil(ncols / float(binning_scale)))
        bin_um = int(round(float(metadata.get("spot_pitch", 2.0)) * binning_scale))
        mask_key = f"square_{bin_um:03d}um"
        if mask_key not in h5_file["masks"]:
            available = ", ".join(h5_file["masks"].keys())
            raise ValueError(f"Missing {mask_key} mask in feature_slice.h5. Available masks: {available}")

        mask_rows, mask_cols, _ = _coo_group_to_arrays(h5_file["masks"][mask_key])
        mask_rows = mask_rows.astype(np.int64)
        mask_cols = mask_cols.astype(np.int64)
        obs_names = np.asarray([f"{mask_key}_{r}_{c}" for r, c in zip(mask_rows, mask_cols)], dtype=object)

        obs_lookup = np.full(nrows_binned * ncols_binned, -1, dtype=np.int64)
        obs_linear = mask_rows * ncols_binned + mask_cols
        obs_lookup[obs_linear] = np.arange(obs_names.size, dtype=np.int64)

        features_group = h5_file["features"]
        gene_names = _decode_h5_strings(features_group["name"][:])
        gene_ids = _decode_h5_strings(features_group["id"][:])
        feature_types = _decode_h5_strings(features_group["feature_type"][:])
        genomes = _decode_h5_strings(features_group["genome"][:])
        feature_slices = h5_file["feature_slices"]
        feature_indices = [
            idx for idx, feature_type in enumerate(feature_types)
            if feature_type == "Gene Expression" and str(idx) in feature_slices
        ]
        if not feature_indices:
            raise ValueError("No Gene Expression feature slices found in feature_slice.h5.")

        row_chunks = []
        col_chunks = []
        data_chunks = []
        var_rows = []
        for out_idx, feature_idx in enumerate(feature_indices):
            group = feature_slices[str(feature_idx)]
            rows, cols, values = _coo_group_to_arrays(group)
            if values.size:
                binned_rows = rows.astype(np.int64) // int(binning_scale)
                binned_cols = cols.astype(np.int64) // int(binning_scale)
                linear = binned_rows * ncols_binned + binned_cols
                valid = (linear >= 0) & (linear < obs_lookup.size)
                obs_idx = obs_lookup[linear[valid]]
                values = values[valid]
                valid_obs = obs_idx >= 0
                obs_idx = obs_idx[valid_obs]
                values = values[valid_obs]
                if obs_idx.size:
                    unique_obs, inverse = np.unique(obs_idx, return_inverse=True)
                    summed = np.bincount(inverse, weights=values.astype(np.float64))
                    nonzero = summed > 0
                    unique_obs = unique_obs[nonzero]
                    summed = summed[nonzero]
                    row_chunks.append(unique_obs.astype(np.int32, copy=False))
                    col_chunks.append(np.full(unique_obs.size, out_idx, dtype=np.int32))
                    data_chunks.append(summed.astype(np.float32, copy=False))

            var_rows.append({
                "gene_ids": gene_ids[feature_idx],
                "feature_type": feature_types[feature_idx],
                "genome": genomes[feature_idx],
            })

        if not row_chunks:
            raise ValueError("Feature slices did not overlap the selected tissue mask.")

        row = np.concatenate(row_chunks)
        col = np.concatenate(col_chunks)
        data = np.concatenate(data_chunks)
        matrix = sp.coo_matrix(
            (data, (row, col)),
            shape=(int(obs_names.size), int(len(feature_indices))),
            dtype=np.float32,
        ).tocsr()
        matrix.sum_duplicates()

        spatial = _microscope_spatial_from_bins(mask_rows, mask_cols, metadata, binning_scale)
        obs = pd.DataFrame({
            "array_row": mask_rows,
            "array_col": mask_cols,
            "bin_size_um": bin_um,
        }, index=obs_names)
        var = pd.DataFrame(var_rows, index=[gene_names[idx] for idx in feature_indices])
        adata = ad.AnnData(X=matrix, obs=obs, var=var)
        adata.var_names_make_unique()
        adata.obsm["spatial"] = spatial
        adata.uns["spatial"] = {
            "scalefactors": {
                "bin_diameter_fullres": _bin_diameter_fullres(metadata, binning_scale),
            },
            "metadata": metadata,
        }
        adata.uns["source_h5_format"] = "10x_feature_slice"
        adata.uns["binning_scale"] = int(binning_scale)
        adata.uns["bin_size_um"] = int(bin_um)
        adata.write_h5ad(output_path)

    return {
        "output_path": str(output_path),
        "n_spots": int(adata.n_obs),
        "n_genes": int(adata.n_vars),
        "bin_size_um": int(bin_um),
    }


def main(argv=None):
    parser = argparse.ArgumentParser(description="Convert 10x Visium HD feature_slice.h5 to h5ad.")
    parser.add_argument("source", help="Input 10x feature_slice.h5 path")
    parser.add_argument("output", help="Output .h5ad path")
    parser.add_argument(
        "-binning-scale",
        type=int,
        default=8,
        help="Number of 2 um bins per output bin. Default 8 creates 16 um bins.",
    )
    args = parser.parse_args(argv)

    summary = convert_feature_slice_h5_to_h5ad(args.source, args.output, binning_scale=args.binning_scale)
    print(
        "Converted {output_path}: {n_spots:,} spots, {n_genes:,} genes, {bin_size_um} um bins".format(
            **summary
        )
    )


if __name__ == "__main__":
    main()
