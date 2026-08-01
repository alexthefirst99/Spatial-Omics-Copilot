"""
DEG Result Models
=================
Thin re-export shim. ``GeneStat`` and ``DEGResult`` now live in
``rag.contracts`` (Person 6's shared contracts module); this module keeps the
``rag.deg.models`` import path stable for ``rag.deg.extraction`` and anything
else that already imports from here.

``to_dict()`` remains the stable surface for callers outside ``rag.deg`` —
see ``rag.contracts`` for field documentation.
"""

from __future__ import annotations

from rag.contracts import (
    MESSAGE_NO_DATA,
    SOURCE_EMPTY,
    SOURCE_RAW_COUNTS,
    SOURCE_UNKNOWN,
    STATUS_BARCODE_MISMATCH,
    STATUS_EMPTY_SELECTION,
    STATUS_ERROR,
    STATUS_INVALID_INPUT,
    STATUS_NO_DATA,
    STATUS_NO_SIGNIFICANT,
    STATUS_OK,
    DEGResult,
    GeneStat,
)

__all__ = [
    "DEGResult",
    "GeneStat",
    "MESSAGE_NO_DATA",
    "SOURCE_EMPTY",
    "SOURCE_RAW_COUNTS",
    "SOURCE_UNKNOWN",
    "STATUS_BARCODE_MISMATCH",
    "STATUS_EMPTY_SELECTION",
    "STATUS_ERROR",
    "STATUS_INVALID_INPUT",
    "STATUS_NO_DATA",
    "STATUS_NO_SIGNIFICANT",
    "STATUS_OK",
]
