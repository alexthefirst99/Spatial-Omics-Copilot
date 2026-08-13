"""Resolve spatial coordinates into the pixel frame used by the slide image.

Most AnnData files already store image-pixel coordinates in ``obsm["spatial"]``.
Converted 10x feature-slice files are different: their primary coordinates are
microscope pixels, while the displayed H&E is commonly the CytAssist image.
Those files retain both transforms in ``uns["spatial"]["metadata"]``.  This
module chooses the coordinate frame that actually fits the displayed image.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import numpy as np


@dataclass(frozen=True)
class SpatialCoordinateResolution:
    coordinates: np.ndarray
    source: str
    spot_diameter: float | None = None


def _positive_float(value: Any) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if np.isfinite(number) and number > 0 else None


def _image_bounds(image_size: Any) -> tuple[float, float] | None:
    """Return ``(width, height)`` from the app's ``[height, width]`` value."""

    if not isinstance(image_size, (list, tuple, np.ndarray)) or len(image_size) < 2:
        return None
    height = _positive_float(image_size[0])
    width = _positive_float(image_size[1])
    if height is None or width is None:
        return None
    return width, height


def _fullres_diameter(adata: Any) -> float | None:
    spatial_uns = adata.uns.get("spatial", {}) if hasattr(adata, "uns") else {}
    if not isinstance(spatial_uns, dict):
        return None
    scalefactors = spatial_uns.get("scalefactors", {})
    if not isinstance(scalefactors, dict):
        return None
    for key in (
        "spot_diameter_fullres",
        "bin_diameter_fullres",
        "spot_diameter",
        "bin_size_fullres",
        "fiducial_diameter_fullres",
    ):
        diameter = _positive_float(scalefactors.get(key))
        if diameter is not None:
            return diameter
    return None


def _apply_homogeneous_transform(points: np.ndarray, matrix: Any) -> np.ndarray | None:
    try:
        transform = np.asarray(matrix, dtype=np.float64)
    except (TypeError, ValueError):
        return None
    if transform.shape != (3, 3) or not np.all(np.isfinite(transform)):
        return None

    homogeneous = np.column_stack([points, np.ones(points.shape[0], dtype=np.float64)])
    mapped = homogeneous @ transform.T
    w = mapped[:, 2]
    valid_w = np.isfinite(w) & (np.abs(w) > 1e-12)
    result = np.full((points.shape[0], 2), np.nan, dtype=np.float64)
    result[valid_w] = mapped[valid_w, :2] / w[valid_w, None]
    return result


def _feature_slice_candidates(adata: Any) -> list[SpatialCoordinateResolution]:
    """Build image-frame candidates retained by the feature-slice converter."""

    if not hasattr(adata, "obs") or "array_row" not in adata.obs or "array_col" not in adata.obs:
        return []

    spatial_uns = adata.uns.get("spatial", {}) if hasattr(adata, "uns") else {}
    metadata = spatial_uns.get("metadata", {}) if isinstance(spatial_uns, dict) else {}
    transforms = metadata.get("transform_matrices", {}) if isinstance(metadata, dict) else {}
    if not isinstance(transforms, dict):
        return []

    scale = _positive_float(adata.uns.get("binning_scale", 1.0)) or 1.0
    try:
        # The converter stores binned array indices; transforms expect the
        # corresponding unbinned spot centre in col/row order.
        cols = np.asarray(adata.obs["array_col"], dtype=np.float64)
        rows = np.asarray(adata.obs["array_row"], dtype=np.float64)
        spot_centres = np.column_stack([(cols + 0.5) * scale, (rows + 0.5) * scale])
    except (TypeError, ValueError):
        return []

    candidates: list[SpatialCoordinateResolution] = []
    for name, matrix in transforms.items():
        if not (str(name).startswith("spot_colrow_to_") and str(name).endswith("_colrow")):
            continue
        mapped = _apply_homogeneous_transform(spot_centres, matrix)
        if mapped is None:
            continue

        transform = np.asarray(matrix, dtype=np.float64)
        x_scale = float(np.linalg.norm(transform[:2, 0]))
        y_scale = float(np.linalg.norm(transform[:2, 1]))
        diameter = scale * (x_scale + y_scale) / 2.0
        candidates.append(
            SpatialCoordinateResolution(
                coordinates=mapped,
                source=str(name),
                spot_diameter=diameter if np.isfinite(diameter) and diameter > 0 else None,
            )
        )
    return candidates


def _fraction_inside_image(coordinates: np.ndarray, bounds: tuple[float, float]) -> float:
    width, height = bounds
    x = coordinates[:, 0]
    y = coordinates[:, 1]
    finite = np.isfinite(x) & np.isfinite(y)
    if not finite.any():
        return 0.0
    inside = finite & (x >= 0) & (x <= width) & (y >= 0) & (y <= height)
    return float(inside.sum()) / float(finite.sum())


def _count_inside_roi_bounds(coordinates: np.ndarray, roi_bounds: Any) -> int:
    if not isinstance(roi_bounds, (list, tuple, np.ndarray)) or len(roi_bounds) < 4:
        return 0
    try:
        min_x, min_y, max_x, max_y = map(float, roi_bounds[:4])
    except (TypeError, ValueError):
        return 0
    x = coordinates[:, 0]
    y = coordinates[:, 1]
    return int(
        np.count_nonzero(
            np.isfinite(x)
            & np.isfinite(y)
            & (x >= min_x)
            & (x <= max_x)
            & (y >= min_y)
            & (y <= max_y)
        )
    )


def resolve_image_spatial_coordinates(
    adata: Any,
    *,
    image_size: Any = None,
    roi_bounds: Any = None,
) -> SpatialCoordinateResolution:
    """Choose the spatial coordinate frame that fits the displayed image.

    The existing ``obsm["spatial"]`` frame remains the default. A metadata-
    derived frame is selected only when it fits the known image substantially
    better, or when the default frame has no points in the ROI bounds and a
    transformed frame does. This keeps ordinary already-aligned h5ad files
    unchanged.
    """

    primary = np.asarray(adata.obsm["spatial"], dtype=np.float64)
    if primary.ndim != 2 or primary.shape[1] < 2:
        return SpatialCoordinateResolution(primary, 'obsm["spatial"]')
    primary = primary[:, :2]
    candidates = [
        SpatialCoordinateResolution(
            coordinates=primary,
            source='obsm["spatial"]',
            spot_diameter=_fullres_diameter(adata),
        ),
        *_feature_slice_candidates(adata),
    ]

    bounds = _image_bounds(image_size)
    if bounds is not None and len(candidates) > 1:
        scores = [_fraction_inside_image(candidate.coordinates, bounds) for candidate in candidates]
        best_index = int(np.argmax(scores))
        # Require a material improvement so an already-correct primary frame
        # wins ties and near-ties.
        if scores[best_index] >= 0.25 and scores[best_index] > scores[0] + 0.05:
            return candidates[best_index]

    if roi_bounds is not None and len(candidates) > 1:
        counts = [_count_inside_roi_bounds(candidate.coordinates, roi_bounds) for candidate in candidates]
        if counts[0] == 0 and max(counts[1:], default=0) > 0:
            return candidates[int(np.argmax(counts))]

    return candidates[0]


__all__ = ["SpatialCoordinateResolution", "resolve_image_spatial_coordinates"]
