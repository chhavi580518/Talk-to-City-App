"""
ingest.py
---------
Loads city chunk JSON files into PGVector.

Chunk file selection (per city):
  - *_chunks_large.json  → preferred (rechunked)
  - *_chunks.json        → fallback (original)
  Never loads both for the same city.

Embedding model + collection is controlled by EMBEDDING_MODEL env var:
  EMBEDDING_MODEL=minilm  →  collection: talktocity_chunks_minilm
  EMBEDDING_MODEL=labse   →  collection: talktocity_chunks_labse

Each collection is independent. Re-run ingest after switching models.

Safe re-run behaviour:
  - If the exact same chunk_ids are already in DB → skips them (no-op)
  - If switching from small → large chunks → clears old chunks for
    that city first, then inserts the new large ones cleanly

Usage:
    python ingest.py
    EMBEDDING_MODEL=labse python ingest.py
"""

import json
import glob
import psycopg
from pathlib import Path
from rag_core import vector_store, DATABASE_URL, COLLECTION_NAME, EMBEDDING_MODEL

RAW_DB_URL = DATABASE_URL.replace("postgresql+psycopg://", "postgresql://")


# ── DB helpers ─────────────────────────────────────────────────────────────

def get_existing_chunk_ids_by_city() -> dict[str, set[str]]:
    """Return {city: {chunk_ids}} for all chunks currently in the vector store."""
    result: dict[str, set[str]] = {}
    try:
        with psycopg.connect(RAW_DB_URL) as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    SELECT e.cmetadata->>'city', e.cmetadata->>'chunk_id'
                    FROM langchain_pg_embedding e
                    JOIN langchain_pg_collection c
                      ON e.collection_id = c.uuid
                    WHERE c.name = %s
                    """,
                    (COLLECTION_NAME,),
                )
                for city, chunk_id in cur.fetchall():
                    if city and chunk_id:
                        result.setdefault(city, set()).add(chunk_id)
    except Exception:
        pass
    return result


def delete_chunks_for_city(city: str) -> int:
    """Delete all chunks for a city from the vector store. Returns count deleted."""
    deleted = 0
    try:
        with psycopg.connect(RAW_DB_URL) as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    DELETE FROM langchain_pg_embedding
                    WHERE id IN (
                        SELECT e.id
                        FROM langchain_pg_embedding e
                        JOIN langchain_pg_collection c
                          ON e.collection_id = c.uuid
                        WHERE c.name = %s
                          AND e.cmetadata->>'city' = %s
                    )
                    """,
                    (COLLECTION_NAME, city),
                )
                deleted = cur.rowcount
            conn.commit()
    except Exception as e:
        print(f"  Warning: could not delete chunks for {city}: {e}")
    return deleted


# ── File selection ──────────────────────────────────────────────────────────

def find_chunk_files() -> list[str]:
    """
    Find one chunk file per city.
    Large chunks take priority over original — never loads both.
    """
    city_files: dict[str, str] = {}

    for path in sorted(glob.glob("data/*_chunks*.json")):
        p = Path(path)
        city = p.stem.replace("_chunks_large", "").replace("_chunks", "")
        is_large = "_large" in p.stem
        if city not in city_files or is_large:
            city_files[city] = path

    chosen = sorted(city_files.values())
    print("Chunk files selected for ingest:")
    for f in chosen:
        tag = "large (rechunked)" if "_large" in f else "original"
        print(f"  {f}  [{tag}]")
    return chosen


# ── Main ───────────────────────────────────────────────────────────────────

def main() -> None:
    from rag_core import EMBEDDING_MODEL, COLLECTION_NAME, EMBEDDING_MODEL_NAME
    print(f"\nEmbedding model : {EMBEDDING_MODEL} ({EMBEDDING_MODEL_NAME})")
    print(f"Target collection: {COLLECTION_NAME}\n")
    chunk_files = find_chunk_files()
    if not chunk_files:
        raise FileNotFoundError("No chunk files found in data/")

    # Load all chunks grouped by city
    chunks_by_city: dict[str, list[dict]] = {}
    for file_path in chunk_files:
        with open(file_path, "r", encoding="utf-8") as f:
            chunks = json.load(f)
        for chunk in chunks:
            city = chunk.get("city", "unknown")
            chunks_by_city.setdefault(city, []).append(chunk)

    total_loaded = sum(len(v) for v in chunks_by_city.items())
    print(f"\nLoaded chunks: { {c: len(v) for c, v in chunks_by_city.items()} }")

    # Get what's currently in the DB per city
    existing_by_city = get_existing_chunk_ids_by_city()

    texts, metadatas, ids = [], [], []

    for city, chunks in chunks_by_city.items():
        incoming_ids = {c["chunk_id"] for c in chunks if c.get("chunk_id")}
        existing_ids = existing_by_city.get(city, set())

        if not existing_ids:
            # City not in DB yet — insert all
            print(f"\n{city}: not in DB, inserting {len(chunks)} chunks...")
            for chunk in chunks:
                if not chunk.get("chunk_id"):
                    continue
                texts.append(chunk["text"])
                metadatas.append(_build_metadata(chunk))
                ids.append(f"{EMBEDDING_MODEL}_{chunk['chunk_id']}")

        elif incoming_ids == existing_ids:
            # Exact same chunk_ids — already up to date, skip
            print(f"\n{city}: already up to date ({len(existing_ids)} chunks), skipping.")

        else:
            # Different chunk_ids — city was rechunked or data changed.
            # Delete old chunks and insert new ones cleanly.
            print(f"\n{city}: chunk_ids changed (old={len(existing_ids)}, new={len(incoming_ids)})")
            print(f"  Clearing old chunks...")
            deleted = delete_chunks_for_city(city)
            print(f"  Deleted {deleted} old chunks.")
            for chunk in chunks:
                if not chunk.get("chunk_id"):
                    continue
                texts.append(chunk["text"])
                metadatas.append(_build_metadata(chunk))
                ids.append(f"{EMBEDDING_MODEL}_{chunk['chunk_id']}")
            print(f"  Queued {len(chunks)} new chunks for insert.")

    if not texts:
        print("\nNothing to insert.")
        return

    print(f"\nInserting {len(texts)} chunks into vector store...")

    # Insert in batches of 20 to avoid memory issues with large embedding models
    BATCH_SIZE = 20
    total_inserted = 0
    for i in range(0, len(texts), BATCH_SIZE):
        batch_texts     = texts[i:i + BATCH_SIZE]
        batch_metadatas = metadatas[i:i + BATCH_SIZE]
        batch_ids       = ids[i:i + BATCH_SIZE]
        result = vector_store.add_texts(
            texts=batch_texts,
            metadatas=batch_metadatas,
            ids=batch_ids,
        )
        total_inserted += len(result)
        print(f"  Batch {i // BATCH_SIZE + 1}: inserted {len(result)} chunks")

    print(f"Done. Inserted {total_inserted} chunks successfully.")

    # Verify the insert actually committed
    import psycopg as _psycopg
    with _psycopg.connect(RAW_DB_URL) as conn:
        with conn.cursor() as cur:
            cur.execute("""
                SELECT COUNT(*) FROM langchain_pg_embedding e
                JOIN langchain_pg_collection c ON e.collection_id = c.uuid
                WHERE c.name = %s
            """, (COLLECTION_NAME,))
            count = cur.fetchone()[0]
    print(f"Verified: {count} chunks now in collection '{COLLECTION_NAME}'")


def _build_metadata(chunk: dict) -> dict:
    return {
        "doc_id":     chunk.get("doc_id"),
        "chunk_id": f"{EMBEDDING_MODEL}_{chunk.get('chunk_id')}",
        "city":       chunk.get("city"),
        "country":    chunk.get("country"),
        "source":     chunk.get("source"),
        "source_url": chunk.get("source_url"),
        "section":    chunk.get("section"),
        "subsection": chunk.get("subsection"),
        "tags":       chunk.get("tags", []),
    }


if __name__ == "__main__":
    main()
