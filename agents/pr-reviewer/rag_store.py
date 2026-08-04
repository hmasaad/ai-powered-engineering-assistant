#!/usr/bin/env python3
"""Minimal SQLite vector store for local Ollama RAG."""

from __future__ import annotations

import fnmatch
import json
import math
import os
import re
import sqlite3
import struct
import urllib.error
import urllib.request
from dataclasses import dataclass
from pathlib import Path


AGENT_DIR = Path(__file__).resolve().parent
ROOT = AGENT_DIR.parents[1]
DEFAULT_DB = Path(
    os.environ.get(
        "PR_REVIEW_INDEX",
        str(AGENT_DIR / "index" / "rag.sqlite"),
    )
)
DEFAULT_EMBED_MODEL = os.environ.get("OLLAMA_EMBED_MODEL", "nomic-embed-text")
OLLAMA_HOST = os.environ.get("OLLAMA_HOST", "http://127.0.0.1:11434").rstrip("/")

INDEX_GLOBS = (
    "lib/**/*.dart",
    "agents/pr-reviewer/*.md",
    "README.md",
    "assets/data/*.json",
    "pubspec.yaml",
)

CHUNK_CHARS = 1_800
CHUNK_OVERLAP = 200


@dataclass
class ChunkHit:
    path: str
    start_line: int
    end_line: int
    content: str
    score: float


def ensure_ollama_reachable() -> None:
    try:
        with urllib.request.urlopen(f"{OLLAMA_HOST}/api/tags", timeout=3) as resp:
            resp.read()
    except Exception as exc:  # noqa: BLE001
        raise RuntimeError(
            f"Ollama is not reachable at {OLLAMA_HOST}. "
            "Install from https://ollama.com then run `ollama serve`."
        ) from exc


def ensure_embed_model(model: str = DEFAULT_EMBED_MODEL) -> None:
    ensure_ollama_reachable()
    with urllib.request.urlopen(f"{OLLAMA_HOST}/api/tags", timeout=3) as resp:
        payload = json.loads(resp.read().decode())
    models = {m.get("name", "") for m in payload.get("models", [])}
    short = model.split(":")[0]
    if model not in models and not any(m.startswith(short) for m in models):
        raise RuntimeError(
            f"Embedding model '{model}' not found. Run: ollama pull {model}"
        )


def embed_text(text: str, model: str = DEFAULT_EMBED_MODEL) -> list[float]:
    body = {"model": model, "prompt": text[:8_000]}
    req = urllib.request.Request(
        f"{OLLAMA_HOST}/api/embeddings",
        data=json.dumps(body).encode(),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=120) as resp:
            payload = json.loads(resp.read().decode())
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode(errors="replace")
        raise RuntimeError(f"Ollama embed failed: {exc.code} {detail}") from exc

    embedding = payload.get("embedding")
    if not isinstance(embedding, list) or not embedding:
        raise RuntimeError(f"Unexpected embed response: {payload}")
    return [float(x) for x in embedding]


def pack_embedding(values: list[float]) -> bytes:
    return struct.pack(f"{len(values)}f", *values)


def unpack_embedding(blob: bytes) -> list[float]:
    n = len(blob) // 4
    return list(struct.unpack(f"{n}f", blob))


def cosine(a: list[float], b: list[float]) -> float:
    if not a or not b or len(a) != len(b):
        return -1.0
    dot = 0.0
    na = 0.0
    nb = 0.0
    for x, y in zip(a, b):
        dot += x * y
        na += x * x
        nb += y * y
    if na <= 0 or nb <= 0:
        return -1.0
    return dot / (math.sqrt(na) * math.sqrt(nb))


def connect(db_path: Path = DEFAULT_DB) -> sqlite3.Connection:
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(db_path))
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS chunks (
            id INTEGER PRIMARY KEY,
            path TEXT NOT NULL,
            start_line INTEGER NOT NULL,
            end_line INTEGER NOT NULL,
            content TEXT NOT NULL,
            embedding BLOB NOT NULL,
            embed_model TEXT NOT NULL,
            dims INTEGER NOT NULL
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS meta (
            key TEXT PRIMARY KEY,
            value TEXT NOT NULL
        )
        """
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_chunks_path ON chunks(path)"
    )
    conn.commit()
    return conn


def clear_index(conn: sqlite3.Connection) -> None:
    conn.execute("DELETE FROM chunks")
    conn.execute("DELETE FROM meta")
    conn.commit()


def delete_chunks_for_path(conn: sqlite3.Connection, path: str) -> int:
    cur = conn.execute("DELETE FROM chunks WHERE path = ?", (path,))
    conn.commit()
    return int(cur.rowcount or 0)


def delete_chunks_for_paths(conn: sqlite3.Connection, paths: list[str]) -> int:
    removed = 0
    for path in sorted(set(paths)):
        removed += delete_chunks_for_path(conn, path)
    return removed


def chunk_count(conn: sqlite3.Connection) -> int:
    row = conn.execute("SELECT COUNT(*) FROM chunks").fetchone()
    return int(row[0]) if row else 0


def indexed_file_count(conn: sqlite3.Connection) -> int:
    row = conn.execute("SELECT COUNT(DISTINCT path) FROM chunks").fetchone()
    return int(row[0]) if row else 0


def is_indexable_relpath(rel: str) -> bool:
    """True if path matches INDEX_GLOBS (posix-style relative path)."""
    # Path.match does not treat ** like root.glob(); fnmatch does for our patterns.
    normalized = rel.replace("\\", "/").lstrip("./")
    return any(fnmatch.fnmatch(normalized, pattern) for pattern in INDEX_GLOBS)


def set_meta(conn: sqlite3.Connection, key: str, value: str) -> None:
    conn.execute(
        "INSERT INTO meta(key, value) VALUES(?, ?) "
        "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
        (key, value),
    )
    conn.commit()


def get_meta(conn: sqlite3.Connection, key: str) -> str | None:
    row = conn.execute("SELECT value FROM meta WHERE key = ?", (key,)).fetchone()
    return None if row is None else str(row[0])


def insert_chunk(
    conn: sqlite3.Connection,
    *,
    path: str,
    start_line: int,
    end_line: int,
    content: str,
    embedding: list[float],
    embed_model: str,
) -> None:
    conn.execute(
        """
        INSERT INTO chunks(path, start_line, end_line, content, embedding, embed_model, dims)
        VALUES (?, ?, ?, ?, ?, ?, ?)
        """,
        (
            path,
            start_line,
            end_line,
            content,
            pack_embedding(embedding),
            embed_model,
            len(embedding),
        ),
    )


def iter_source_files(root: Path = ROOT) -> list[Path]:
    files: list[Path] = []
    for pattern in INDEX_GLOBS:
        files.extend(root.glob(pattern))
    # de-dupe while preserving order
    seen: set[Path] = set()
    unique: list[Path] = []
    for path in files:
        resolved = path.resolve()
        if resolved in seen or not path.is_file():
            continue
        seen.add(resolved)
        unique.append(path)
    return unique


def chunk_file(path: Path, root: Path = ROOT) -> list[tuple[int, int, str]]:
    text = path.read_text(encoding="utf-8", errors="replace")
    lines = text.splitlines()
    if not lines:
        return []

    rel = str(path.relative_to(root))
    chunks: list[tuple[int, int, str]] = []

    # Prefer symbol-ish splits for Dart
    if path.suffix == ".dart":
        starts = [0]
        for i, line in enumerate(lines):
            if re.match(
                r"^(abstract\s+)?(class|enum|mixin|extension|typedef)\s+\w+",
                line,
            ) or re.match(r"^(Future<.*>|[A-Za-z_<>]+)\s+\w+\s*\(", line):
                if i > 0:
                    starts.append(i)
        starts = sorted(set(starts))
        for idx, start in enumerate(starts):
            end = starts[idx + 1] if idx + 1 < len(starts) else len(lines)
            block_lines = lines[start:end]
            block = "\n".join(block_lines).strip()
            if not block:
                continue
            if len(block) <= CHUNK_CHARS:
                chunks.append((start + 1, end, f"// file: {rel}\n{block}"))
            else:
                chunks.extend(
                    _window_chunks(block_lines, start, rel),
                )
        return chunks

    return _window_chunks(lines, 0, rel)


def _window_chunks(
    lines: list[str],
    absolute_start_index: int,
    rel: str,
) -> list[tuple[int, int, str]]:
    chunks: list[tuple[int, int, str]] = []
    i = 0
    while i < len(lines):
        buf: list[str] = []
        chars = 0
        j = i
        while j < len(lines) and chars + len(lines[j]) + 1 <= CHUNK_CHARS:
            buf.append(lines[j])
            chars += len(lines[j]) + 1
            j += 1
        if not buf:
            buf = [lines[i][:CHUNK_CHARS]]
            j = i + 1
        start_line = absolute_start_index + i + 1
        end_line = absolute_start_index + j
        chunks.append((start_line, end_line, f"// file: {rel}\n" + "\n".join(buf)))
        if j >= len(lines):
            break
        # overlap by approx CHUNK_OVERLAP chars
        back = 0
        chars = 0
        k = j - 1
        while k > i and chars < CHUNK_OVERLAP:
            chars += len(lines[k]) + 1
            back += 1
            k -= 1
        i = max(i + 1, j - back)
    return chunks


def build_query(diff: str, changed_paths: list[str]) -> str:
    symbols = sorted(
        set(
            re.findall(
                r"\b([A-Z][A-Za-z0-9_]*(?:Bloc|Data|Event|Service|Api|Screen|Contract)?)\b",
                diff,
            )
        )
    )[:40]
    layers = []
    joined = " ".join(changed_paths)
    for token in ("bloc", "contract", "service", "api", "ui", "inject"):
        if token in joined:
            layers.append(token)
    return (
        "Flutter salon booking BLoC PR review context.\n"
        f"Changed files: {', '.join(changed_paths[:30])}\n"
        f"Layers: {', '.join(layers) or 'general'}\n"
        f"Symbols: {', '.join(symbols) or 'none'}\n"
        f"Diff excerpt:\n{diff[:2_500]}"
    )


def search(
    query_embedding: list[float],
    *,
    top_k: int = 8,
    db_path: Path = DEFAULT_DB,
    prefer_paths: list[str] | None = None,
) -> list[ChunkHit]:
    if not db_path.is_file():
        return []

    conn = connect(db_path)
    rows = conn.execute(
        "SELECT path, start_line, end_line, content, embedding FROM chunks"
    ).fetchall()
    conn.close()

    prefer = set(prefer_paths or [])
    hits: list[ChunkHit] = []
    for path, start_line, end_line, content, blob in rows:
        emb = unpack_embedding(blob)
        score = cosine(query_embedding, emb)
        # light boost for same-path / sibling-path chunks
        if path in prefer:
            score += 0.05
        else:
            for p in prefer:
                if path.startswith(str(Path(p).parent)) or p.startswith(
                    str(Path(path).parent)
                ):
                    score += 0.02
                    break
        hits.append(
            ChunkHit(
                path=path,
                start_line=int(start_line),
                end_line=int(end_line),
                content=content,
                score=score,
            )
        )

    hits.sort(key=lambda h: h.score, reverse=True)

    # de-dupe near-identical path ranges
    selected: list[ChunkHit] = []
    seen_keys: set[str] = set()
    for hit in hits:
        key = f"{hit.path}:{hit.start_line}"
        if key in seen_keys:
            continue
        seen_keys.add(key)
        selected.append(hit)
        if len(selected) >= top_k:
            break
    return selected


def format_hits(hits: list[ChunkHit]) -> str:
    if not hits:
        return "(no retrieved context — run scripts/pr-review-index.sh first)"
    parts: list[str] = []
    for hit in hits:
        parts.append(
            f"### {hit.path}:{hit.start_line}-{hit.end_line} "
            f"(score={hit.score:.3f})\n```\n{hit.content}\n```"
        )
    return "\n\n".join(parts)
