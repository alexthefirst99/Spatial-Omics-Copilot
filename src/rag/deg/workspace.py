"""
Workspace Path Resolution and Validation
========================================
``work_dir`` and ``folder_id`` are used to build filesystem paths, so they are
a genuine path-traversal surface: ``folder_id`` is interpolated directly into
``f"{work_dir}/user{folder_id}/..."``, meaning a value like ``"../../.."``
would escape the workspace entirely. Everything is validated and containment-
checked here before any file is opened.

Layering note (reported, not unilaterally fixed)
------------------------
The canonical path builders ``_spatial_omics_state_path`` and
``_spatial_omics_cluster_path`` live in ``niceview.interface.upload``, which is
the app/UI layer. ``docs/rules.md`` section 3 forbids ``src/rag/`` importing
from the app layer, and that import also forms a cycle:
``rag.deg -> niceview.interface.upload -> app.status_store + rag.clustering``.

Two mitigations are applied here, both entirely inside ``rag.deg``:

1. The resolvers are **injectable** — every public entry point accepts
   ``state_path_resolver`` / ``cluster_path_resolver``, so callers (and tests)
   can supply their own and never touch niceview at all.
2. The default import is **lazy and function-local**, so merely importing
   ``rag.deg`` no longer transitively imports dash, anndata, or
   ``app.status_store``.

Resolving the coupling properly means moving the path builders into a shared
location; that is a Person 6 item and is written up in
``docs/validation/person2_deg_notes.md``.

This module also avoids ``niceview.utils.io``, whose module-scope imports pull
in OpenCV, pandas and toml. For the ``.json`` / ``.h5ad`` paths handled here,
``vio.exists`` and ``vio.load_json`` are exactly ``os.path.exists`` and
``json.load`` — the only special-casing in ``vio.exists`` is a cache-miss hack
for pyramidal OME-TIFFs, which cannot apply to these paths.
"""

from __future__ import annotations

import json
import logging
import os
import re
from typing import Any, Callable

logger = logging.getLogger(__name__)

# A folder id becomes part of a directory name. Restrict it to characters that
# cannot express traversal, separators, drive letters, or shell metacharacters.
_SAFE_FOLDER_ID_RE = re.compile(r"\A[A-Za-z0-9_-]{0,64}\Z")

PathResolver = Callable[[str, str], str]


class WorkspacePathError(ValueError):
    """Raised when a workspace path is unsafe or unusable.

    Never propagates out of ``rag.deg``; callers convert it into a sanitized
    status message.
    """


def validate_folder_id(folder_id: object) -> str:
    """Validate a user-supplied folder id.

    Args:
        folder_id: Candidate folder id. The empty string is the normal
            single-workspace case and is allowed.

    Returns:
        The validated folder id.

    Raises:
        WorkspacePathError: If the value is not a string, contains a null byte,
            a path separator, a parent reference, or any character outside
            ``[A-Za-z0-9_-]``.
    """

    if folder_id is None:
        return ""
    if not isinstance(folder_id, str):
        raise WorkspacePathError("Workspace folder id must be a string.")
    if "\x00" in folder_id:
        raise WorkspacePathError("Workspace folder id contains a null byte.")
    if not _SAFE_FOLDER_ID_RE.match(folder_id):
        # Covers "..", "/", "\\", "C:", spaces, and everything else.
        raise WorkspacePathError("Workspace folder id contains illegal characters.")
    return folder_id


def validate_work_dir(work_dir: object) -> str:
    """Validate a workspace root directory.

    Args:
        work_dir: Candidate workspace root.

    Returns:
        The work_dir as a string.

    Raises:
        WorkspacePathError: If it is not a non-empty string or contains a null
            byte.
    """

    if not isinstance(work_dir, str) or not work_dir.strip():
        raise WorkspacePathError("Workspace directory is missing.")
    if "\x00" in work_dir:
        raise WorkspacePathError("Workspace directory contains a null byte.")
    return work_dir


def ensure_within(root: str, candidate: str) -> str:
    """Confirm ``candidate`` resolves inside ``root``.

    Uses ``os.path.realpath`` on both sides so that symlinks, ``..`` segments,
    and mixed separators cannot be used to escape the workspace.

    Args:
        root: Directory the path must stay within.
        candidate: Path to check.

    Returns:
        The real (resolved) candidate path.

    Raises:
        WorkspacePathError: If the candidate escapes the root.
    """

    real_root = os.path.realpath(root)
    real_candidate = os.path.realpath(candidate)
    if real_candidate != real_root and not real_candidate.startswith(
        real_root + os.sep
    ):
        # Deliberately does not include either path in the message; callers
        # surface status messages to the UI and the LLM.
        raise WorkspacePathError("Resolved path escapes the workspace directory.")
    return real_candidate


def default_state_path_resolver(work_dir: str, folder_id: str) -> str:
    """Resolve the spatial-omics state path via niceview, imported lazily."""

    from niceview.interface.upload import _spatial_omics_state_path

    return _spatial_omics_state_path(work_dir, folder_id)


def default_cluster_path_resolver(work_dir: str, folder_id: str) -> str:
    """Resolve the spatial-cluster path via niceview, imported lazily."""

    from niceview.interface.upload import _spatial_omics_cluster_path

    return _spatial_omics_cluster_path(work_dir, folder_id)


def resolve_workspace_file(
    work_dir: object,
    folder_id: object,
    resolver: PathResolver,
) -> str:
    """Resolve and containment-check a workspace file path.

    Args:
        work_dir: Workspace root.
        folder_id: User folder id.
        resolver: Callable building the path from (work_dir, folder_id).

    Returns:
        The validated, resolved absolute path.

    Raises:
        WorkspacePathError: If validation fails, the resolver misbehaves, or
            the result escapes the workspace.
    """

    safe_work_dir = validate_work_dir(work_dir)
    safe_folder_id = validate_folder_id(folder_id)

    try:
        raw_path = resolver(safe_work_dir, safe_folder_id)
    except WorkspacePathError:
        raise
    except Exception as exc:  # resolver is injectable; treat it defensively
        raise WorkspacePathError("Workspace path could not be resolved.") from exc

    if not isinstance(raw_path, str) or not raw_path.strip():
        raise WorkspacePathError("Workspace path could not be resolved.")
    if "\x00" in raw_path:
        raise WorkspacePathError("Workspace path contains a null byte.")

    return ensure_within(safe_work_dir, raw_path)


def read_json(path: str) -> dict[str, Any]:
    """Read a JSON object from disk.

    Args:
        path: File to read.

    Returns:
        The parsed object.

    Raises:
        WorkspacePathError: If the file is missing, unreadable, not valid JSON,
            or does not contain a JSON object.
    """

    try:
        with open(path, "r", encoding="utf-8") as handle:
            payload = json.load(handle)
    except (OSError, ValueError, UnicodeDecodeError) as exc:
        raise WorkspacePathError("Workspace state file could not be read.") from exc

    if not isinstance(payload, dict):
        raise WorkspacePathError("Workspace state file has an unexpected format.")
    return payload


def resolve_h5ad_path(work_dir: str, state: dict[str, Any]) -> str:
    """Extract and containment-check the h5ad path recorded in workspace state.

    The stored path comes from a JSON file on disk, so it is treated as
    untrusted and re-checked against the workspace root rather than opened
    directly.

    Args:
        work_dir: Workspace root the file must live under.
        state: Parsed ``spatial_omics.json`` contents.

    Returns:
        The validated, resolved h5ad path.

    Raises:
        WorkspacePathError: If the entry is missing, malformed, escapes the
            workspace, or does not exist on disk.
    """

    raw_path = state.get("h5ad_path")
    if not isinstance(raw_path, str) or not raw_path.strip():
        raise WorkspacePathError("No h5ad path is recorded for this workspace.")
    if "\x00" in raw_path:
        raise WorkspacePathError("Recorded h5ad path contains a null byte.")

    resolved = ensure_within(work_dir, raw_path)
    if not os.path.exists(resolved):
        raise WorkspacePathError("Recorded h5ad file does not exist.")
    return resolved


__all__ = [
    "PathResolver",
    "WorkspacePathError",
    "default_cluster_path_resolver",
    "default_state_path_resolver",
    "ensure_within",
    "read_json",
    "resolve_h5ad_path",
    "resolve_workspace_file",
    "validate_folder_id",
    "validate_work_dir",
]
