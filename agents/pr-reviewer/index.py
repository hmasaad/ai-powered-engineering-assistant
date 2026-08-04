#!/usr/bin/env python3
"""Build or incrementally update the SQLite RAG index."""

from __future__ import annotations

import argparse
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

AGENT_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(AGENT_DIR))

from rag_store import (  # noqa: E402
    DEFAULT_DB,
    DEFAULT_EMBED_MODEL,
    ROOT,
    chunk_count,
    chunk_file,
    clear_index,
    connect,
    delete_chunks_for_paths,
    embed_text,
    ensure_embed_model,
    indexed_file_count,
    insert_chunk,
    is_indexable_relpath,
    iter_source_files,
    set_meta,
)


def run_git(args: list[str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        args,
        cwd=ROOT,
        text=True,
        capture_output=True,
        check=False,
    )


def parse_name_status(diff_text: str) -> tuple[list[str], list[str]]:
    """Return (updated_paths, deleted_paths) from git diff --name-status."""
    updated: list[str] = []
    deleted: list[str] = []
    for line in diff_text.splitlines():
        line = line.strip()
        if not line:
            continue
        parts = line.split("\t")
        status = parts[0]
        if status.startswith("R") and len(parts) >= 3:
            deleted.append(parts[1])
            updated.append(parts[2])
        elif status.startswith("D") and len(parts) >= 2:
            deleted.append(parts[1])
        elif len(parts) >= 2:
            updated.append(parts[1])
    return updated, deleted


def changed_paths_since(since_ref: str, until_ref: str = "HEAD") -> tuple[list[str], list[str]]:
    if since_ref and set(since_ref) == {"0"}:
        # GitHub first-push sentinel: treat as full rebuild signal
        return [], []
    result = run_git(["git", "diff", "--name-status", f"{since_ref}...{until_ref}"])
    if result.returncode != 0:
        raise RuntimeError(
            f"git diff failed: {(result.stderr or result.stdout).strip()}"
        )
    return parse_name_status(result.stdout)


def index_file(
    conn,
    path: Path,
    *,
    embed_model: str,
) -> int:
    rel = str(path.relative_to(ROOT)).replace("\\", "/")
    chunks = chunk_file(path, ROOT)
    for start_line, end_line, content in chunks:
        embedding = embed_text(content, model=embed_model)
        insert_chunk(
            conn,
            path=rel,
            start_line=start_line,
            end_line=end_line,
            content=content,
            embedding=embedding,
            embed_model=embed_model,
        )
    return len(chunks)


def full_rebuild(conn, *, embed_model: str) -> tuple[int, int]:
    clear_index(conn)
    files = iter_source_files(ROOT)
    total_chunks = 0
    for path in files:
        rel = str(path.relative_to(ROOT))
        n = index_file(conn, path, embed_model=embed_model)
        sys.stderr.write(f"Indexed {rel} ({n} chunks)\n")
        total_chunks += n
        conn.commit()
    return len(files), total_chunks


def incremental_update(
    conn,
    *,
    updated: list[str],
    deleted: list[str],
    embed_model: str,
) -> tuple[int, int, int]:
    """Returns (files_touched, chunks_removed, chunks_added)."""
    touch = sorted(
        {
            p.replace("\\", "/").lstrip("./")
            for p in (updated + deleted)
            if is_indexable_relpath(p.replace("\\", "/").lstrip("./"))
        }
    )
    if not touch:
        sys.stderr.write("No indexable paths changed.\n")
        return 0, 0, 0

    removed = delete_chunks_for_paths(conn, touch)
    added = 0
    for rel in touch:
        path = ROOT / rel
        if not path.is_file():
            sys.stderr.write(f"Removed embeddings for deleted file: {rel}\n")
            continue
        n = index_file(conn, path, embed_model=embed_model)
        added += n
        sys.stderr.write(f"Re-indexed {rel} ({n} chunks)\n")
        conn.commit()
    return len(touch), removed, added


def write_meta(conn, *, embed_model: str, mode: str) -> None:
    set_meta(conn, "embed_model", embed_model)
    set_meta(conn, "built_at", datetime.now(timezone.utc).isoformat())
    set_meta(conn, "update_mode", mode)
    set_meta(conn, "file_count", str(indexed_file_count(conn)))
    set_meta(conn, "chunk_count", str(chunk_count(conn)))


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Full or incremental SQLite RAG index update",
    )
    parser.add_argument("--db", default=str(DEFAULT_DB), help="SQLite db path")
    parser.add_argument(
        "--embed-model",
        default=DEFAULT_EMBED_MODEL,
        help=f"Ollama embedding model (default: {DEFAULT_EMBED_MODEL})",
    )
    parser.add_argument(
        "--full",
        action="store_true",
        help="Rebuild the entire index",
    )
    parser.add_argument(
        "--since",
        default="",
        help="Git ref/sha to diff from (with --until) for incremental update",
    )
    parser.add_argument(
        "--until",
        default="HEAD",
        help="Git ref/sha to diff to (default: HEAD)",
    )
    parser.add_argument(
        "--files",
        nargs="*",
        default=[],
        help="Explicit relative file paths to re-index (deletes old chunks first)",
    )
    parser.add_argument(
        "--deleted",
        nargs="*",
        default=[],
        help="Explicit relative file paths to remove from the index",
    )
    args = parser.parse_args()

    try:
        ensure_embed_model(args.embed_model)
    except RuntimeError as exc:
        sys.stderr.write(f"{exc}\n")
        return 1

    db_path = Path(args.db)
    conn = connect(db_path)
    empty = chunk_count(conn) == 0

    try:
        # Empty DB must always full-rebuild — incremental would leave most of
        # the codebase missing from the index.
        if args.full or empty:
            if empty and not args.full:
                sys.stderr.write("Index empty — performing full rebuild.\n")
            mode = "full"
            files_n, chunks_n = full_rebuild(conn, embed_model=args.embed_model)
            write_meta(conn, embed_model=args.embed_model, mode=mode)
            sys.stderr.write(
                f"Full index: {chunks_n} chunks from {files_n} files → {db_path}\n"
            )
            return 0

        updated = list(args.files)
        deleted = list(args.deleted)
        if args.since:
            u, d = changed_paths_since(args.since, args.until)
            if not u and not d and set(args.since) == {"0"}:
                sys.stderr.write(
                    "Detected empty before-sha — falling back to full rebuild.\n"
                )
                files_n, chunks_n = full_rebuild(conn, embed_model=args.embed_model)
                write_meta(conn, embed_model=args.embed_model, mode="full")
                sys.stderr.write(
                    f"Full index: {chunks_n} chunks from {files_n} files → {db_path}\n"
                )
                return 0
            updated.extend(u)
            deleted.extend(d)


        if not updated and not deleted:
            sys.stderr.write(
                "Nothing to update. Pass --full, --since <sha>, or --files.\n"
            )
            return 0

        mode = "incremental"
        touched, removed, added = incremental_update(
            conn,
            updated=updated,
            deleted=deleted,
            embed_model=args.embed_model,
        )
        write_meta(conn, embed_model=args.embed_model, mode=mode)
        sys.stderr.write(
            f"Incremental update: touched={touched} removed_chunks={removed} "
            f"added_chunks={added} → {db_path}\n"
        )
        return 0
    finally:
        conn.close()


if __name__ == "__main__":
    raise SystemExit(main())
