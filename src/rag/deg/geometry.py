"""
ROI Polygon Validation and Spot Selection
=========================================
ROI coordinates arrive from a browser drawing tool and are therefore UNTRUSTED
input. Everything here validates before it computes: types, finiteness,
vertex count, magnitude, and geometric validity are all checked before a
coordinate list is handed to shapely.

Selection semantics are ``covers`` (boundary-inclusive), matching the original
per-point ``Polygon.covers(Point(x, y))`` implementation exactly. A spot lying
on an ROI edge is inside the ROI, as before.

Performance: point-in-polygon runs as a numpy bounding-box prefilter followed
by a single vectorised ``shapely.covers`` call over the survivors. The original
implementation built one ``Point`` object and made one Python-level ``covers``
call per spot per polygon, which is O(n_spots) interpreter round-trips; at
30 000 spots that dominated the whole DEG path.
"""

from __future__ import annotations

import logging
from collections.abc import Sequence
from typing import Any

import numpy as np
import shapely
from shapely.geometry import Polygon

logger = logging.getLogger(__name__)

# Minimum vertices for a polygon ring.
MIN_POLYGON_VERTICES = 3

# Coordinate magnitude ceiling. Pixel coordinates on a gigapixel slide stay far
# below this; anything larger indicates corrupt or hostile input.
MAX_COORDINATE_MAGNITUDE = 1e12


class PolygonValidationError(ValueError):
    """Raised internally when an ROI polygon cannot be used.

    Never propagates out of ``rag.deg``: callers convert it into a status
    message on an empty result.
    """


def _validate_vertex(vertex: Any) -> tuple[float, float]:
    """Validate a single (x, y) vertex.

    Args:
        vertex: Candidate vertex; must be a 2-element sequence of finite reals.

    Returns:
        The vertex as a tuple of floats.

    Raises:
        PolygonValidationError: If the vertex is malformed, non-finite, or
            implausibly large.
    """

    if isinstance(vertex, (str, bytes)) or not isinstance(vertex, Sequence):
        raise PolygonValidationError("ROI vertex is not a coordinate pair.")
    if len(vertex) != 2:
        raise PolygonValidationError("ROI vertex must have exactly 2 values.")

    coords: list[float] = []
    for value in vertex:
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            raise PolygonValidationError("ROI coordinate is not a number.")
        number = float(value)
        if not np.isfinite(number):
            raise PolygonValidationError("ROI coordinate is not finite.")
        if abs(number) > MAX_COORDINATE_MAGNITUDE:
            raise PolygonValidationError("ROI coordinate is out of range.")
        coords.append(number)
    return coords[0], coords[1]


def validate_polygon(ring: Any) -> Polygon:
    """Validate one ROI ring and build a shapely polygon from it.

    Args:
        ring: Sequence of ``(x, y)`` vertices.

    Returns:
        A valid shapely ``Polygon``.

    Raises:
        PolygonValidationError: If the ring is not a sequence, has too few
            vertices, contains malformed coordinates, or produces a degenerate
            or self-intersecting polygon.
    """

    if isinstance(ring, (str, bytes)) or not isinstance(ring, Sequence):
        raise PolygonValidationError("ROI polygon is not a list of vertices.")
    if len(ring) < MIN_POLYGON_VERTICES:
        raise PolygonValidationError(
            f"ROI polygon needs at least {MIN_POLYGON_VERTICES} vertices."
        )

    vertices = [_validate_vertex(vertex) for vertex in ring]

    try:
        polygon = Polygon(vertices)
    except (ValueError, TypeError) as exc:
        raise PolygonValidationError("ROI polygon could not be built.") from exc

    # A bow-tie / self-intersecting ring is invalid; so is a zero-area sliver.
    if polygon.is_empty or not polygon.is_valid:
        raise PolygonValidationError("ROI polygon is degenerate or self-intersecting.")
    if not np.isfinite(polygon.area) or polygon.area <= 0.0:
        raise PolygonValidationError("ROI polygon has no area.")
    return polygon


def validate_polygons(coords: Any) -> list[Polygon]:
    """Validate a list of ROI rings, skipping individually bad ones.

    A single malformed ring does not discard an otherwise usable multi-part
    ROI; it is logged and skipped. Only an ROI with no usable ring at all is
    rejected.

    Args:
        coords: Sequence of coordinate rings.

    Returns:
        The list of valid polygons.

    Raises:
        PolygonValidationError: If ``coords`` is not a sequence of rings, or no
            ring survived validation.
    """

    if coords is None:
        raise PolygonValidationError("No ROI coordinates were provided.")
    if isinstance(coords, (str, bytes)) or not isinstance(coords, Sequence):
        raise PolygonValidationError("ROI coordinates are not a list.")
    if len(coords) == 0:
        raise PolygonValidationError("No ROI coordinates were provided.")

    polygons: list[Polygon] = []
    rejected = 0
    for ring in coords:
        try:
            polygons.append(validate_polygon(ring))
        except PolygonValidationError as exc:
            rejected += 1
            logger.debug("Skipping invalid ROI ring: %s", exc)

    if not polygons:
        raise PolygonValidationError("No valid ROI polygon was provided.")
    if rejected:
        logger.info("Ignored %d invalid ROI ring(s).", rejected)
    return polygons


def build_roi_mask(
    spatial: np.ndarray,
    polygons: Sequence[Polygon],
) -> np.ndarray:
    """Mark which spots fall inside any ROI polygon.

    Computed once per run and reused for every gene. For each polygon a numpy
    bounding-box comparison narrows the candidate spots, then a single
    vectorised ``shapely.covers`` call resolves the exact test on that subset.
    Spots outside the bounding box cannot be covered, so skipping them is
    exact, not approximate.

    Args:
        spatial: Array of shape ``(n_spots, 2)`` with x/y coordinates.
        polygons: Validated polygons; a spot inside any of them is selected.

    Returns:
        A boolean array of length ``n_spots``.
    """

    coords = np.asarray(spatial, dtype=np.float64)
    if coords.ndim != 2 or coords.shape[1] < 2:
        raise PolygonValidationError("Spatial coordinates are malformed.")

    n_spots = int(coords.shape[0])
    selected = np.zeros(n_spots, dtype=bool)
    if n_spots == 0:
        return selected

    x = coords[:, 0]
    y = coords[:, 1]
    # Spots with non-finite coordinates can never be inside a finite polygon.
    finite = np.isfinite(x) & np.isfinite(y)

    for polygon in polygons:
        min_x, min_y, max_x, max_y = polygon.bounds
        in_box = (
            finite
            & (x >= min_x)
            & (x <= max_x)
            & (y >= min_y)
            & (y <= max_y)
            & ~selected  # already-selected spots need no further testing
        )
        candidate_idx = np.flatnonzero(in_box)
        if candidate_idx.size == 0:
            continue

        shapely.prepare(polygon)
        points = shapely.points(x[candidate_idx], y[candidate_idx])
        hits = shapely.covers(polygon, points)
        selected[candidate_idx[np.asarray(hits, dtype=bool)]] = True

    return selected


__all__ = [
    "MAX_COORDINATE_MAGNITUDE",
    "MIN_POLYGON_VERTICES",
    "PolygonValidationError",
    "build_roi_mask",
    "validate_polygon",
    "validate_polygons",
]
