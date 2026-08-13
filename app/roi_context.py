"""Synchronize ROI DEG context between the Dash selection and chat callbacks.

Dash and Flask handle requests on different threads.  A chat request can
therefore arrive after ``coords.json`` is written but before the selection
callback has finished calculating and writing ``roi_context.json``.  This
module gives both paths one versioned, locked way to build that context.
"""

from __future__ import annotations

import hashlib
import json
import os
import threading
from typing import Any, Callable


_LOCKS: dict[str, threading.Lock] = {}
_LOCKS_GUARD = threading.Lock()


def _context_path(work_dir: str, folder_id: str = "") -> str:
    return os.path.join(work_dir, f"user{folder_id}", "roi_context.json")


def roi_signature(coords: Any) -> str:
    """Return a stable version identifier for normalized ROI coordinates."""

    encoded = json.dumps(
        coords,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=True,
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _lock_for(path: str) -> threading.Lock:
    with _LOCKS_GUARD:
        return _LOCKS.setdefault(path, threading.Lock())


def _load_matching_context(path: str, signature: str) -> dict[str, Any] | None:
    try:
        with open(path, encoding="utf-8") as handle:
            payload = json.load(handle)
    except (FileNotFoundError, json.JSONDecodeError, OSError, TypeError):
        return None

    if not isinstance(payload, dict) or payload.get("roi_signature") != signature:
        return None
    return payload


def _atomic_dump_json(payload: dict[str, Any], path: str) -> None:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp_path = f"{path}.{threading.get_ident()}.tmp"
    try:
        with open(tmp_path, "w", encoding="utf-8") as handle:
            json.dump(payload, handle)
        os.replace(tmp_path, path)
    finally:
        try:
            os.remove(tmp_path)
        except FileNotFoundError:
            pass


def ensure_roi_context(
    work_dir: str,
    coords: Any,
    folder_id: str = "",
    top_n: int = 25,
    *,
    compute: Callable[..., dict[str, Any] | None] | None = None,
) -> dict[str, Any] | None:
    """Return DEG context for exactly ``coords``, computing it when necessary.

    Concurrent callers share a per-workspace lock.  In the usual race, the
    chat request waits for the already-running selection calculation and then
    consumes its result.  If the selection callback has not started yet, chat
    performs the calculation itself so the first question is still grounded.
    Empty DEG results are cached too, avoiding repeated expensive attempts.
    """

    if not coords:
        return None

    path = _context_path(work_dir, folder_id)
    signature = roi_signature(coords)

    with _lock_for(path):
        cached = _load_matching_context(path, signature)
        if cached is not None:
            return cached

        if compute is None:
            from rag.deg import get_roi_high_expression_genes

            compute = get_roi_high_expression_genes

        result = compute(work_dir, coords, folder_id=folder_id, top_n=top_n)
        gene_objects = (
            result.get("top_genes", [])
            if isinstance(result, dict) and result.get("top_genes")
            else []
        )
        payload = {
            "roi_signature": signature,
            "gene_objects": gene_objects,
            "analysis_available": isinstance(result, dict),
            "selected_spots": result.get("selected_spots", 0)
            if isinstance(result, dict)
            else 0,
            "status": result.get("status", "") if isinstance(result, dict) else "",
            "status_message": result.get("status_message", "")
            if isinstance(result, dict)
            else "",
        }
        _atomic_dump_json(payload, path)
        return payload


__all__ = ["ensure_roi_context", "roi_signature"]
