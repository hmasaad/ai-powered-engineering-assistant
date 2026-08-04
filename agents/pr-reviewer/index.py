#!/usr/bin/env python3
"""Build the minimal SQLite RAG index for the local PR reviewer."""

from __future__ import annotations

import argparse
import sys
from datetime import datetime, timezone
from pathlib import Path

AGENT_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(AGENT_DIR))

from rag_store import (  # noqa: E402
    DEFAULT_DB,
    DEFAULT_EMBED_MODEL,
    ROOT,
    chunk_file,
    clear_index,
    connect,
    embed_text,
    ensure_embed_model,
    insert_chunk,
    iter_source_files,
    set_meta,
)


def main() -> int:
    parser = argparse.ArgumentParser(description="Index repo into SQLite for RAG")
    parser.add_argument("--db", default=str(DEFAULT_DB), help="SQLite db path")
    parser.add_argument(
        "--embed-model",
        default=DEFAULT_EMBED_MODEL,
        help=f"Ollama embedding model (default: {DEFAULT_EMBED_MODEL})",
    )
    args = parser.parse_args()

    try:
        ensure_embed_model(args.embed_model)
    except RuntimeError as exc:
        sys.stderr.write(f"{exc}\n")
        return 1

    files = iter_source_files(ROOT)
    if not files:
        sys.stderr.write("No source files found to index.\n")
        return 1

    db_path = Path(args.db)
    conn = connect(db_path)
    clear_index(conn)

    total_chunks = 0
    for path in files:
        rel = str(path.relative_to(ROOT))
        chunks = chunk_file(path, ROOT)
        sys.stderr.write(f"Indexing {rel} ({len(chunks)} chunks)...\n")
        for start_line, end_line, content in chunks:
            try:
                embedding = embed_text(content, model=args.embed_model)
            except RuntimeError as exc:
                sys.stderr.write(f"Embed failed for {rel}:{start_line}: {exc}\n")
                return 1
            insert_chunk(
                conn,
                path=rel,
                start_line=start_line,
                end_line=end_line,
                content=content,
                embedding=embedding,
                embed_model=args.embed_model,
            )
            total_chunks += 1
        conn.commit()

    set_meta(conn, "embed_model", args.embed_model)
    set_meta(conn, "built_at", datetime.now(timezone.utc).isoformat())
    set_meta(conn, "file_count", str(len(files)))
    set_meta(conn, "chunk_count", str(total_chunks))
    conn.close()

    sys.stderr.write(
        f"Indexed {total_chunks} chunks from {len(files)} files → {db_path}\n"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
