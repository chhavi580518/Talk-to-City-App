"""
rechunk.py
----------
Merges small chunks into larger ones to improve RAG answer quality.

The current chunks average 315 chars (~60 words) which is too small —
the LLM doesn't get enough context per retrieval. This script merges
adjacent chunks from the same section until they reach TARGET_SIZE.

Strategy:
- Merge consecutive chunks that share the same city + section
- Stop merging when combined text exceeds TARGET_SIZE chars
- Never merge across different sections (preserves topical coherence)
- Preserve all metadata from the first chunk in each merged group
- Write new *_chunks_large.json files alongside the originals
  (ingest.py auto-detects and prefers these — no manual config needed)

Usage:
    python rechunk.py                     # uses defaults
    python rechunk.py --target 1200       # custom target size
    python rechunk.py --input data/       # custom input dir
"""

import json
import glob
import argparse
from pathlib import Path

# ── Config ─────────────────────────────────────────────────────────────────

TARGET_SIZE  = 1000   # target chars per chunk (~200 words) — sweet spot for RAG
MAX_SIZE     = 1500   # hard ceiling — never exceed this


def merge_chunks(chunks: list[dict], target: int = TARGET_SIZE) -> list[dict]:
    """
    Merge consecutive chunks from the same section into larger chunks.

    Merging rules:
    - Only merge chunks with the same city AND section
    - Accumulate until combined text >= target OR next chunk is different section
    - Never exceed MAX_SIZE
    - New chunk_id = first_chunk_id + "_merged" if multiple chunks combined
    - Metadata taken from first chunk in group; text joined with newline
    """
    if not chunks:
        return []

    merged = []
    buffer = [chunks[0]]

    for chunk in chunks[1:]:
        current  = buffer[-1]
        combined_len = sum(len(c["text"]) for c in buffer) + len(chunk["text"])

        same_section = (
            chunk.get("city") == current.get("city") and
            chunk.get("section") == current.get("section")
        )

        if same_section and combined_len <= MAX_SIZE:
            buffer.append(chunk)
        else:
            merged.append(_flush(buffer))
            buffer = [chunk]

    if buffer:
        merged.append(_flush(buffer))

    return merged


def _flush(buffer: list[dict]) -> dict:
    """Combine a buffer of chunks into one merged chunk."""
    if len(buffer) == 1:
        return buffer[0]

    base = dict(buffer[0])  # copy metadata from first chunk
    base["text"]     = "\n\n".join(c["text"].strip() for c in buffer)
    base["chunk_id"] = buffer[0]["chunk_id"] + "_merged"

    # Merge tags from all chunks (deduplicated)
    all_tags = []
    seen = set()
    for c in buffer:
        for tag in c.get("tags", []):
            if tag not in seen:
                all_tags.append(tag)
                seen.add(tag)
    base["tags"] = all_tags

    return base


def rechunk_file(input_path: str, target: int = TARGET_SIZE) -> str:
    """Rechunk a single *_chunks.json file and write a new *_chunks_large.json."""
    with open(input_path, "r", encoding="utf-8") as f:
        chunks = json.load(f)

    original_count = len(chunks)
    original_avg   = int(sum(len(c["text"]) for c in chunks) / len(chunks))

    merged = merge_chunks(chunks, target)

    new_avg   = int(sum(len(c["text"]) for c in merged) / len(merged))
    out_path  = input_path.replace("_chunks.json", "_chunks_large.json")

    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(merged, f, ensure_ascii=False, indent=2)

    print(f"{Path(input_path).name}")
    print(f"  Before: {original_count} chunks, avg {original_avg} chars")
    print(f"  After:  {len(merged)} chunks, avg {new_avg} chars")
    print(f"  Saved:  {out_path}")
    print()

    return out_path


def main():
    parser = argparse.ArgumentParser(description="Merge small RAG chunks into larger ones")
    parser.add_argument("--input",  default="data/",  help="Input directory containing *_chunks.json files")
    parser.add_argument("--target", default=TARGET_SIZE, type=int, help=f"Target chunk size in chars (default: {TARGET_SIZE})")
    args = parser.parse_args()

    files = glob.glob(f"{args.input}*_chunks.json")
    # Exclude already-merged files
    files = [f for f in files if "_large" not in f]

    if not files:
        print(f"No *_chunks.json files found in {args.input}")
        return

    print(f"Rechunking {len(files)} files with target size {args.target} chars...\n")
    for f in sorted(files):
        rechunk_file(f, args.target)

    print("Done. ingest.py will automatically detect and use the *_chunks_large.json files.")
    print("Re-ingest with fresh data:")
    print("  1. Clear old chunks from DB:")
    print("     podman exec talktocity-db psql -U postgres -d talktocity -c \"DELETE FROM langchain_pg_embedding;\"")
    print("  2. Re-run ingest:")
    print("     podman exec talktocity-backend python ingest.py")


if __name__ == "__main__":
    main()
