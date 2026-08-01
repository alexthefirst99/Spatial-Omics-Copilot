"""Differential expression extraction — public API.

Per ``docs/rules.md`` section 4, callers import from this ``__init__`` only,
never from the implementation modules directly.

``run_roi_deg`` is the primary entry point and returns a ``DEGResult``.
``get_roi_high_expression_genes`` / ``get_cluster_high_expression_genes`` are
retained with unchanged signatures and dict output for existing callers in
``app.py``; see ``docs/validation/person2_deg_notes.md`` for the migration
path.

``MESSAGE_NO_DATA`` is re-exported so callers outside this package can surface
the exact T-044 wording without duplicating the literal.
"""

from rag.deg.extraction import (
    get_cluster_high_expression_genes,
    get_roi_high_expression_genes,
    run_roi_deg,
)
from rag.deg.filtering import filter_deg_candidates
from rag.deg.models import DEGResult, GeneStat, MESSAGE_NO_DATA

__all__ = [
    "DEGResult",
    "GeneStat",
    "MESSAGE_NO_DATA",
    "filter_deg_candidates",
    "get_cluster_high_expression_genes",
    "get_roi_high_expression_genes",
    "run_roi_deg",
]
