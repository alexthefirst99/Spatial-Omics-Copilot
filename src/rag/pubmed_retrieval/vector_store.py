"""Semantic search over the abstracts in a PubMed retrieval result.

ChromaDB is intentionally imported only when no client is supplied.  Besides
keeping Chroma an optional dependency, this makes the module straightforward
to test with a small in-memory client or collection double.
"""

from __future__ import annotations

from collections.abc import Mapping
from hashlib import sha256
import json
import os
from pathlib import Path
from typing import Any


_DEFAULT_COLLECTION_NAME = "spatial_omics_pubmed"
_DEFAULT_PERSIST_DIRECTORY = Path("data") / "pubmed_chroma"


def _value(source: object, name: str, default: Any = "") -> Any:
    """Read a field from either a mapping or an object."""

    if isinstance(source, Mapping):
        return source.get(name, default)
    return getattr(source, name, default)


def _as_text(value: Any) -> str:
    if value is None:
        return ""
    return str(value).strip()


def _normalise_year(value: Any) -> int | None:
    """Return a useful public representation of a publication year."""

    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value if 1000 <= value <= 9999 else None

    text = str(value).strip()
    if len(text) == 4 and text.isdigit():
        year = int(text)
        return year if 1000 <= year <= 9999 else None
    return None


def _extract_papers(pubmed_result: object) -> list[object]:
    papers = _value(pubmed_result, "papers", None)
    if papers is None or isinstance(papers, (str, bytes, Mapping)):
        return []
    try:
        return list(papers)
    except TypeError:
        return []


def _prepare_records(papers: list[object]) -> list[dict[str, Any]]:
    """Convert heterogeneous paper objects into stable Chroma records."""

    records_by_id: dict[str, dict[str, Any]] = {}

    for paper in papers:
        if paper is None:
            continue

        title = _as_text(_value(paper, "title"))
        abstract = _as_text(_value(paper, "abstract"))
        # Supporting the legacy field costs nothing and lets callers migrate
        # from the project's previous PubMed schema without losing text.
        if not abstract:
            abstract = _as_text(_value(paper, "snippet"))
        if not title and not abstract:
            continue

        pmid = _as_text(_value(paper, "pmid"))
        if not pmid.isdigit():
            pmid = ""
        journal = _as_text(_value(paper, "journal"))
        year = _normalise_year(_value(paper, "year", ""))
        document = "\n\n".join(part for part in (title, abstract) if part)

        # A real PubMed record has a PMID.  The content digest keeps malformed
        # but otherwise useful test/input records deterministic as well.
        record_id = pmid or "anonymous-" + sha256(
            document.encode("utf-8")
        ).hexdigest()
        if record_id in records_by_id:
            continue

        records_by_id[record_id] = {
            "id": record_id,
            "document": document,
            "pmid": pmid,
            "title": title,
            "abstract": abstract,
            "journal": journal,
            "year": year,
        }

    return list(records_by_id.values())


def _corpus_id(records: list[dict[str, Any]]) -> str:
    """Build an order-independent identity for exactly this paper corpus."""

    payload = [
        {
            "id": record["id"],
            "document": record["document"],
            "journal": record["journal"],
            "year": record["year"],
        }
        for record in sorted(records, key=lambda item: item["id"])
    ]
    serialised = json.dumps(
        payload,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    )
    return sha256(serialised.encode("utf-8")).hexdigest()


def _default_client(persist_directory: str | os.PathLike[str] | None) -> Any:
    """Create Chroma's persistent client without importing it at module load."""

    import chromadb  # type: ignore[import-not-found]

    configured_path = (
        persist_directory
        if persist_directory is not None
        else os.environ.get("PUBMED_CHROMA_DIR") or _DEFAULT_PERSIST_DIRECTORY
    )
    return chromadb.PersistentClient(path=str(configured_path))


def _collection_from_client(client: Any, collection_name: str) -> Any:
    """Accept either a Chroma client or an already-created collection."""

    if callable(getattr(client, "upsert", None)) and callable(
        getattr(client, "query", None)
    ):
        return client

    get_collection = getattr(client, "get_or_create_collection")
    return get_collection(
        name=collection_name,
        configuration={"hnsw": {"space": "cosine"}},
        metadata={"distance_metric": "cosine"},
    )


def _first_result_row(value: Any) -> list[Any]:
    """Normalise Chroma's nested query arrays (and simple test doubles)."""

    if value is None:
        return []
    if not isinstance(value, (list, tuple)):
        return []
    if not value:
        return []
    first = value[0]
    if isinstance(first, (list, tuple)):
        return list(first)
    return list(value)


def _query(
    collection: Any,
    question: str,
    n_results: int,
    corpus_id: str,
) -> Any:
    return collection.query(
        query_texts=[question],
        n_results=n_results,
        where={"corpus_id": corpus_id},
        include=["metadatas", "documents", "distances"],
    )


def _distance_metric(collection: Any) -> str:
    configuration = getattr(collection, "configuration", None)
    if isinstance(configuration, Mapping):
        hnsw = configuration.get("hnsw")
        if isinstance(hnsw, Mapping):
            metric = _as_text(hnsw.get("space")).lower()
            if metric in {"cosine", "l2", "ip"}:
                return metric
    metadata = getattr(collection, "metadata", None)
    if isinstance(metadata, Mapping):
        metric = _as_text(metadata.get("distance_metric")).lower()
        if metric in {"cosine", "l2", "ip"}:
            return metric
    # Collections created by this module are configured as cosine. Test
    # doubles can expose ``configuration`` when simulating another metric.
    return "cosine"


def _similarity_score(
    distance: float | None,
    distance_metric: str,
) -> float | None:
    if distance is None:
        return None
    if distance_metric in {"cosine", "ip"}:
        return 1.0 - distance
    # L2 distance is unbounded, so expose a monotonic [0, 1] convenience score
    # while retaining the raw distance and metric in the public result.
    return 1.0 / (1.0 + max(distance, 0.0))


def semantic_search_abstracts(
    pubmed_result: object,
    question: str,
    top_k: int = 3,
    *,
    persist_directory: str | os.PathLike[str] | None = None,
    collection_name: str = _DEFAULT_COLLECTION_NAME,
    client: Any = None,
) -> list[dict]:
    """Return the abstracts most semantically relevant to ``question``.

    ``pubmed_result`` may be a shared ``PubMedResult`` dataclass or a mapping
    containing ``papers``.  Individual papers may likewise be objects or
    mappings.  Optional-dependency, embedding, persistence, and malformed
    response failures are deliberately converted to an empty result so this
    enrichment step never breaks the main RAG pipeline.
    """

    try:
        question_text = _as_text(question)
        requested = int(top_k)
        if not question_text or requested <= 0 or not collection_name:
            return []

        records = _prepare_records(_extract_papers(pubmed_result))
        if not records:
            return []

        active_corpus_id = _corpus_id(records)
        active_client = (
            client if client is not None else _default_client(persist_directory)
        )
        collection = _collection_from_client(active_client, collection_name)
        distance_metric = _distance_metric(collection)

        # Namespace storage IDs by corpus. The same PMID can legitimately
        # occur in simultaneous searches; a bare PMID would let one request
        # overwrite another request's corpus metadata before it queries.
        ids = [
            f"{active_corpus_id}:{record['id']}"
            for record in records
        ]
        documents = [record["document"] for record in records]
        metadatas = [
            {
                "pmid": record["pmid"],
                "title": record["title"],
                "abstract": record["abstract"],
                "journal": record["journal"],
                # Chroma metadata values cannot be null.  Keep ``None`` in the
                # public result while using an empty scalar in persistence.
                "year": record["year"] if record["year"] is not None else "",
                "corpus_id": active_corpus_id,
            }
            for record in records
        ]
        collection.upsert(ids=ids, documents=documents, metadatas=metadatas)

        raw_result = _query(
            collection,
            question_text,
            min(requested, len(records)),
            active_corpus_id,
        )
        if not isinstance(raw_result, Mapping):
            return []

        result_ids = _first_result_row(raw_result.get("ids"))
        result_metadata = _first_result_row(raw_result.get("metadatas"))
        result_distances = _first_result_row(raw_result.get("distances"))
        result_documents = _first_result_row(raw_result.get("documents"))

        by_id = {
            storage_id: record
            for storage_id, record in zip(ids, records, strict=True)
        }
        by_pmid = {
            record["pmid"]: record for record in records if record["pmid"]
        }
        row_count = max(
            len(result_ids),
            len(result_metadata),
            len(result_distances),
            len(result_documents),
        )

        matches: list[dict] = []
        seen_ids: set[str] = set()
        for index in range(row_count):
            returned_id = (
                _as_text(result_ids[index]) if index < len(result_ids) else ""
            )
            metadata = (
                result_metadata[index]
                if index < len(result_metadata)
                and isinstance(result_metadata[index], Mapping)
                else {}
            )
            returned_pmid = _as_text(metadata.get("pmid"))

            # Resolve every hit back to the current input.  This second guard
            # prevents stale papers leaking in even if a lightweight fake
            # collection ignores Chroma's ``where`` filter.
            record = by_id.get(returned_id) or by_pmid.get(returned_pmid)
            if record is None or record["id"] in seen_ids:
                continue

            raw_distance = (
                result_distances[index]
                if index < len(result_distances)
                else None
            )
            try:
                distance = (
                    float(raw_distance) if raw_distance is not None else None
                )
            except (TypeError, ValueError):
                distance = None

            matches.append(
                {
                    "pmid": record["pmid"],
                    "title": record["title"],
                    "abstract": record["abstract"],
                    "journal": record["journal"],
                    "year": record["year"],
                    "similarity_score": _similarity_score(
                        distance,
                        distance_metric,
                    ),
                    "distance": distance,
                    "distance_metric": distance_metric,
                }
            )
            seen_ids.add(record["id"])
            if len(matches) >= requested:
                break

        return matches
    except Exception:
        return []


__all__ = ["semantic_search_abstracts"]
