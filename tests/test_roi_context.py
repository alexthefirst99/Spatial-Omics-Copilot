from __future__ import annotations

import json
from concurrent.futures import ThreadPoolExecutor

from app.roi_context import ensure_roi_context, roi_signature


COORDS = [[[1, 2], [5, 2], [5, 8], [1, 2]]]


def test_roi_signature_is_stable_for_equivalent_mapping_order():
    assert roi_signature({"points": COORDS, "type": "polygon"}) == roi_signature(
        {"type": "polygon", "points": COORDS}
    )


def test_ensure_roi_context_computes_and_versions_cache(tmp_path):
    calls = []

    def compute(work_dir, coords, folder_id="", top_n=25):
        calls.append((work_dir, coords, folder_id, top_n))
        return {
            "top_genes": [{"gene": "EPCAM", "log2_fold_change": 2.5}],
            "selected_spots": 17,
            "status": "ok",
        }

    first = ensure_roi_context(str(tmp_path), COORDS, compute=compute)
    second = ensure_roi_context(str(tmp_path), COORDS, compute=compute)

    assert first == second
    assert first["gene_objects"][0]["gene"] == "EPCAM"
    assert first["analysis_available"] is True
    assert first["roi_signature"] == roi_signature(COORDS)
    assert len(calls) == 1

    saved = json.loads((tmp_path / "user" / "roi_context.json").read_text())
    assert saved == first


def test_changed_roi_never_reuses_previous_gene_context(tmp_path):
    genes = iter(("OLD_ROI_GENE", "NEW_ROI_GENE"))

    def compute(*args, **kwargs):
        return {"top_genes": [{"gene": next(genes)}], "selected_spots": 12}

    old = ensure_roi_context(str(tmp_path), COORDS, compute=compute)
    new_coords = [[[10, 20], [50, 20], [50, 80], [10, 20]]]
    new = ensure_roi_context(str(tmp_path), new_coords, compute=compute)

    assert old["gene_objects"][0]["gene"] == "OLD_ROI_GENE"
    assert new["gene_objects"][0]["gene"] == "NEW_ROI_GENE"
    assert old["roi_signature"] != new["roi_signature"]


def test_legacy_unversioned_context_is_not_trusted(tmp_path):
    context_path = tmp_path / "user" / "roi_context.json"
    context_path.parent.mkdir()
    context_path.write_text(json.dumps({"gene_objects": [{"gene": "STALE"}]}))

    refreshed = ensure_roi_context(
        str(tmp_path),
        COORDS,
        compute=lambda *args, **kwargs: {
            "top_genes": [{"gene": "CURRENT"}],
            "selected_spots": 4,
        },
    )

    assert refreshed["gene_objects"] == [{"gene": "CURRENT"}]
    assert refreshed["roi_signature"] == roi_signature(COORDS)


def test_concurrent_selection_and_chat_compute_only_once(tmp_path):
    calls = 0

    def compute(*args, **kwargs):
        nonlocal calls
        calls += 1
        return {"top_genes": [{"gene": "KRT8"}], "selected_spots": 9}

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(
            pool.map(
                lambda _: ensure_roi_context(str(tmp_path), COORDS, compute=compute),
                range(2),
            )
        )

    assert calls == 1
    assert results[0] == results[1]


def test_unavailable_expression_data_is_cached_explicitly(tmp_path):
    calls = 0

    def compute(*args, **kwargs):
        nonlocal calls
        calls += 1
        return None

    first = ensure_roi_context(str(tmp_path), COORDS, compute=compute)
    second = ensure_roi_context(str(tmp_path), COORDS, compute=compute)

    assert calls == 1
    assert first == second
    assert first["analysis_available"] is False
    assert first["gene_objects"] == []
