#!/usr/bin/env python3
"""Advanced SQLite RAG for local Ollama PR reviews.

Features beyond plain cosine search:
- Hybrid retrieval: dense vectors + FTS5 BM25, fused with RRF
- Chunk metadata: symbols, architecture layer, imports
- Multi-query retrieval (diff / symbols / layers)
- Import- and layer-aware path expansion
- MMR diversification across files
"""

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

SCHEMA_VERSION = "2"

INDEX_GLOBS = (
    "lib/**/*.dart",
    "agents/pr-reviewer/*.md",
    "README.md",
    "assets/data/*.json",
    "pubspec.yaml",
)

CHUNK_CHARS = 1_800
CHUNK_OVERLAP = 200

# Reciprocal Rank Fusion constant
RRF_K = 60

# Layer adjacency for architecture-aware boosting
LAYER_NEIGHBORS: dict[str, tuple[str, ...]] = {
    "bloc": ("contract", "service", "ui", "inject"),
    "contract": ("bloc", "ui"),
    "service": ("bloc", "api"),
    "api": ("service",),
    "ui": ("bloc", "contract"),
    "inject": ("bloc", "service", "api"),
    "core": ("bloc", "contract", "service"),
}


@dataclass
class ChunkHit:
    path: str
    start_line: int
    end_line: int
    content: str
    score: float
    symbols: str = ""
    layer: str = ""
    source: str = ""  # vector | fts | hybrid


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


def infer_layer(path: str) -> str:
    normalized = path.replace("\\", "/")
    if "/blocs/" in normalized or normalized.startswith("lib/blocs/"):
        return "bloc"
    if "/contracts/" in normalized:
        return "contract"
    if "/services/" in normalized:
        return "service"
    if "/api/" in normalized:
        return "api"
    if "/ui/" in normalized:
        return "ui"
    if "/inject/" in normalized:
        return "inject"
    if "/core/" in normalized:
        return "core"
    if normalized.endswith(".md"):
        return "docs"
    if normalized.endswith(".json"):
        return "data"
    return "other"


def extract_symbols(content: str) -> str:
    names: list[str] = []
    for match in re.finditer(
        r"\b(?:class|enum|mixin|extension|typedef)\s+(\w+)",
        content,
    ):
        names.append(match.group(1))
    for match in re.finditer(
        r"^(?:Future<[^>]+>|[A-Za-z_][\w<>?]*)\s+(\w+)\s*\(",
        content,
        re.MULTILINE,
    ):
        name = match.group(1)
        if name not in {"if", "for", "while", "switch", "return", "assert"}:
            names.append(name)
    # de-dupe preserve order
    seen: set[str] = set()
    unique: list[str] = []
    for name in names:
        if name in seen:
            continue
        seen.add(name)
        unique.append(name)
    return " ".join(unique[:40])


def extract_imports(content: str) -> str:
    imports: list[str] = []
    for match in re.finditer(
        r"""^import\s+['"]([^'"]+)['"]""",
        content,
        re.MULTILINE,
    ):
        imports.append(match.group(1))
    return " ".join(imports[:40])


def chunk_metadata(path: str, content: str) -> tuple[str, str, str]:
    """Return (symbols, layer, imports)."""
    return extract_symbols(content), infer_layer(path), extract_imports(content)


def _ensure_column(conn: sqlite3.Connection, table: str, column: str, decl: str) -> None:
    cols = {
        row[1]
        for row in conn.execute(f"PRAGMA table_info({table})").fetchall()
    }
    if column not in cols:
        conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {decl}")


def _fts_available(conn: sqlite3.Connection) -> bool:
    try:
        conn.execute("SELECT 1 FROM chunks_fts LIMIT 1")
        return True
    except sqlite3.Error:
        return False


def _create_fts(conn: sqlite3.Connection) -> None:
    conn.execute("DROP TABLE IF EXISTS chunks_fts")
    conn.execute(
        """
        CREATE VIRTUAL TABLE chunks_fts USING fts5(
            path,
            symbols,
            content,
            layer,
            content='chunks',
            content_rowid='id',
            tokenize='unicode61'
        )
        """
    )
    conn.execute("DROP TRIGGER IF EXISTS chunks_ai")
    conn.execute("DROP TRIGGER IF EXISTS chunks_ad")
    conn.execute("DROP TRIGGER IF EXISTS chunks_au")
    conn.execute(
        """
        CREATE TRIGGER chunks_ai AFTER INSERT ON chunks BEGIN
          INSERT INTO chunks_fts(rowid, path, symbols, content, layer)
          VALUES (new.id, new.path, new.symbols, new.content, new.layer);
        END
        """
    )
    conn.execute(
        """
        CREATE TRIGGER chunks_ad AFTER DELETE ON chunks BEGIN
          INSERT INTO chunks_fts(chunks_fts, rowid, path, symbols, content, layer)
          VALUES ('delete', old.id, old.path, old.symbols, old.content, old.layer);
        END
        """
    )
    conn.execute(
        """
        CREATE TRIGGER chunks_au AFTER UPDATE ON chunks BEGIN
          INSERT INTO chunks_fts(chunks_fts, rowid, path, symbols, content, layer)
          VALUES ('delete', old.id, old.path, old.symbols, old.content, old.layer);
          INSERT INTO chunks_fts(rowid, path, symbols, content, layer)
          VALUES (new.id, new.path, new.symbols, new.content, new.layer);
        END
        """
    )


def _rebuild_fts(conn: sqlite3.Connection) -> None:
    _create_fts(conn)
    conn.execute(
        """
        INSERT INTO chunks_fts(rowid, path, symbols, content, layer)
        SELECT id, path, COALESCE(symbols, ''), content, COALESCE(layer, '')
        FROM chunks
        """
    )


def _backfill_metadata(conn: sqlite3.Connection) -> None:
    rows = conn.execute(
        "SELECT id, path, content, symbols, layer, imports FROM chunks"
    ).fetchall()
    for chunk_id, path, content, symbols, layer, imports in rows:
        if symbols and layer:
            continue
        sym, lay, imps = chunk_metadata(path, content or "")
        conn.execute(
            "UPDATE chunks SET symbols = ?, layer = ?, imports = ? WHERE id = ?",
            (sym, lay, imps, chunk_id),
        )


def migrate_schema(conn: sqlite3.Connection) -> None:
    _ensure_column(conn, "chunks", "symbols", "TEXT NOT NULL DEFAULT ''")
    _ensure_column(conn, "chunks", "layer", "TEXT NOT NULL DEFAULT ''")
    _ensure_column(conn, "chunks", "imports", "TEXT NOT NULL DEFAULT ''")
    _backfill_metadata(conn)
    if not _fts_available(conn):
        _rebuild_fts(conn)
    version = get_meta(conn, "schema_version")
    if version != SCHEMA_VERSION:
        # Ensure FTS is in sync after metadata backfill
        _rebuild_fts(conn)
        set_meta(conn, "schema_version", SCHEMA_VERSION)
        set_meta(conn, "rag_mode", "advanced_hybrid")


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
            dims INTEGER NOT NULL,
            symbols TEXT NOT NULL DEFAULT '',
            layer TEXT NOT NULL DEFAULT '',
            imports TEXT NOT NULL DEFAULT ''
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
    migrate_schema(conn)
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_chunks_layer ON chunks(layer)"
    )
    conn.commit()
    return conn


def clear_index(conn: sqlite3.Connection) -> None:
    conn.execute("DELETE FROM chunks")
    conn.execute("DELETE FROM meta")
    conn.commit()
    _rebuild_fts(conn)
    set_meta(conn, "schema_version", SCHEMA_VERSION)
    set_meta(conn, "rag_mode", "advanced_hybrid")
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
    symbols, layer, imports = chunk_metadata(path, content)
    conn.execute(
        """
        INSERT INTO chunks(
            path, start_line, end_line, content, embedding, embed_model, dims,
            symbols, layer, imports
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            path,
            start_line,
            end_line,
            content,
            pack_embedding(embedding),
            embed_model,
            len(embedding),
            symbols,
            layer,
            imports,
        ),
    )


def iter_source_files(root: Path = ROOT) -> list[Path]:
    files: list[Path] = []
    for pattern in INDEX_GLOBS:
        files.extend(root.glob(pattern))
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

    rel = str(path.relative_to(root)).replace("\\", "/")
    chunks: list[tuple[int, int, str]] = []

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
                chunks.extend(_window_chunks(block_lines, start, rel))
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
        back = 0
        chars = 0
        k = j - 1
        while k > i and chars < CHUNK_OVERLAP:
            chars += len(lines[k]) + 1
            back += 1
            k -= 1
        i = max(i + 1, j - back)
    return chunks


def extract_symbols_from_diff(diff: str) -> list[str]:
    symbols = sorted(
        set(
            re.findall(
                r"\b([A-Z][A-Za-z0-9_]*(?:Bloc|Data|Event|Service|Api|Screen|Contract)?)\b",
                diff,
            )
        )
    )
    return symbols[:40]


def layers_from_paths(paths: list[str]) -> list[str]:
    layers = []
    for path in paths:
        layer = infer_layer(path)
        if layer not in layers and layer not in {"other", "docs", "data"}:
            layers.append(layer)
    return layers


def import_paths_from_files(changed_paths: list[str], root: Path = ROOT) -> list[str]:
    """Resolve package:/relative imports from changed Dart files to repo paths."""
    related: list[str] = []
    package_prefix = "package:salon_booking/"
    for rel in changed_paths:
        full = root / rel
        if full.suffix != ".dart" or not full.is_file():
            continue
        try:
            text = full.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        for raw in extract_imports(text).split():
            if raw.startswith(package_prefix):
                candidate = "lib/" + raw[len(package_prefix) :]
            elif raw.startswith("package:"):
                continue
            else:
                # relative import
                candidate = str((full.parent / raw).resolve().relative_to(root.resolve()))
                candidate = candidate.replace("\\", "/")
            if is_indexable_relpath(candidate) and candidate not in related:
                related.append(candidate)
    return related


def sibling_layer_paths(changed_paths: list[str], root: Path = ROOT) -> list[str]:
    """Guess related contract/bloc/service/api/ui files by feature stem."""
    related: list[str] = []
    stems: set[str] = set()
    for path in changed_paths:
        name = Path(path).stem
        for suffix in (
            "_bloc",
            "_contract",
            "_service",
            "_api",
            "_screen",
            "_data",
            "_event",
        ):
            if name.endswith(suffix):
                stems.add(name[: -len(suffix)])
                break
        else:
            stems.add(name)

    candidates = [
        "lib/blocs/{stem}_bloc.dart",
        "lib/core/contracts/{stem}_contract.dart",
        "lib/services/{stem}_service.dart",
        "lib/api/{stem}_api.dart",
        "lib/ui/home/{stem}_screen.dart",
        "lib/ui/booking/{stem}_screen.dart",
        "lib/ui/salon/{stem}_screen.dart",
    ]
    for stem in stems:
        for template in candidates:
            candidate = template.format(stem=stem)
            if candidate in changed_paths:
                continue
            if (root / candidate).is_file() and candidate not in related:
                related.append(candidate)
    return related


def expand_prefer_paths(changed_paths: list[str], root: Path = ROOT) -> list[str]:
    prefer = list(dict.fromkeys(changed_paths))
    for path in import_paths_from_files(changed_paths, root):
        if path not in prefer:
            prefer.append(path)
    for path in sibling_layer_paths(changed_paths, root):
        if path not in prefer:
            prefer.append(path)
    return prefer


def build_query(diff: str, changed_paths: list[str]) -> str:
    """Primary hybrid query (kept for compatibility)."""
    return build_queries(diff, changed_paths)[0]


def build_queries(diff: str, changed_paths: list[str]) -> list[str]:
    """Multi-query set for advanced retrieval."""
    symbols = extract_symbols_from_diff(diff)
    layers = layers_from_paths(changed_paths)
    primary = (
        "Flutter salon booking BLoC PR review context.\n"
        f"Changed files: {', '.join(changed_paths[:30])}\n"
        f"Layers: {', '.join(layers) or 'general'}\n"
        f"Symbols: {', '.join(symbols) or 'none'}\n"
        f"Diff excerpt:\n{diff[:2_500]}"
    )
    queries = [primary]
    if symbols:
        queries.append(
            "Find definitions and usages for: " + ", ".join(symbols[:20])
        )
    if layers:
        neighbor_bits = []
        for layer in layers:
            for neighbor in LAYER_NEIGHBORS.get(layer, ()):
                neighbor_bits.append(neighbor)
        queries.append(
            "Contract-driven BLoC architecture context for layers: "
            + ", ".join(dict.fromkeys([*layers, *neighbor_bits]))
        )
    # lexical-friendly short query
    lexical_parts: list[str] = []
    for p in changed_paths[:15]:
        lexical_parts.append(Path(p).stem.replace("_", " "))
    lexical_parts.extend(symbols[:15])
    lexical_parts.extend(layers)
    lexical = " ".join(dict.fromkeys(lexical_parts))
    if lexical.strip():
        queries.append(lexical.strip())
    # de-dupe
    seen: set[str] = set()
    unique: list[str] = []
    for q in queries:
        key = q.strip()
        if not key or key in seen:
            continue
        seen.add(key)
        unique.append(key)
    return unique


def _hit_key(path: str, start_line: int) -> str:
    return f"{path}:{start_line}"


def _path_boost(path: str, prefer: set[str], prefer_layers: set[str]) -> float:
    boost = 0.0
    if path in prefer:
        boost += 0.08
    else:
        for p in prefer:
            try:
                if path.startswith(str(Path(p).parent)) or p.startswith(
                    str(Path(path).parent)
                ):
                    boost += 0.03
                    break
            except ValueError:
                continue
    layer = infer_layer(path)
    if layer in prefer_layers:
        boost += 0.03
        for neighbor in LAYER_NEIGHBORS.get(layer, ()):
            if neighbor in prefer_layers:
                boost += 0.01
                break
    return boost


def vector_candidates(
    conn: sqlite3.Connection,
    query_embedding: list[float],
    *,
    prefer: set[str],
    prefer_layers: set[str],
    limit: int,
) -> list[ChunkHit]:
    rows = conn.execute(
        "SELECT path, start_line, end_line, content, embedding, symbols, layer "
        "FROM chunks"
    ).fetchall()
    hits: list[ChunkHit] = []
    for path, start_line, end_line, content, blob, symbols, layer in rows:
        emb = unpack_embedding(blob)
        score = cosine(query_embedding, emb)
        score += _path_boost(path, prefer, prefer_layers)
        hits.append(
            ChunkHit(
                path=path,
                start_line=int(start_line),
                end_line=int(end_line),
                content=content,
                score=score,
                symbols=symbols or "",
                layer=layer or "",
                source="vector",
            )
        )
    hits.sort(key=lambda h: h.score, reverse=True)
    return hits[:limit]


def _fts_query(text: str) -> str:
    """Convert free text into a safe FTS5 OR query of tokens."""
    tokens = re.findall(r"[A-Za-z_][A-Za-z0-9_]{1,}", text)
    cleaned: list[str] = []
    seen: set[str] = set()
    for token in tokens:
        low = token.lower()
        if low in {
            "the",
            "and",
            "for",
            "with",
            "from",
            "this",
            "that",
            "file",
            "diff",
            "none",
            "general",
        }:
            continue
        if low in seen:
            continue
        seen.add(low)
        cleaned.append(token)
        if len(cleaned) >= 24:
            break
    if not cleaned:
        return ""
    return " OR ".join(cleaned)


def fts_candidates(
    conn: sqlite3.Connection,
    query_text: str,
    *,
    prefer: set[str],
    prefer_layers: set[str],
    limit: int,
) -> list[ChunkHit]:
    if not _fts_available(conn):
        return []
    fts = _fts_query(query_text)
    if not fts:
        return []
    try:
        rows = conn.execute(
            """
            SELECT c.path, c.start_line, c.end_line, c.content, c.symbols, c.layer,
                   bm25(chunks_fts) AS rank
            FROM chunks_fts
            JOIN chunks c ON c.id = chunks_fts.rowid
            WHERE chunks_fts MATCH ?
            ORDER BY rank
            LIMIT ?
            """,
            (fts, limit),
        ).fetchall()
    except sqlite3.Error:
        return []

    hits: list[ChunkHit] = []
    for path, start_line, end_line, content, symbols, layer, rank in rows:
        # bm25: lower is better → convert to similarity-like score
        score = 1.0 / (1.0 + max(float(rank), 0.0))
        score += _path_boost(path, prefer, prefer_layers)
        hits.append(
            ChunkHit(
                path=path,
                start_line=int(start_line),
                end_line=int(end_line),
                content=content,
                score=score,
                symbols=symbols or "",
                layer=layer or "",
                source="fts",
            )
        )
    return hits


def rrf_fuse(
    ranked_lists: list[list[ChunkHit]],
    *,
    k: int = RRF_K,
) -> dict[str, tuple[float, ChunkHit]]:
    fused: dict[str, tuple[float, ChunkHit]] = {}
    for hits in ranked_lists:
        for rank, hit in enumerate(hits):
            key = _hit_key(hit.path, hit.start_line)
            add = 1.0 / (k + rank + 1)
            if key in fused:
                score, prev = fused[key]
                # keep richest metadata / higher individual score as representative
                chosen = hit if hit.score >= prev.score else prev
                fused[key] = (score + add, ChunkHit(
                    path=chosen.path,
                    start_line=chosen.start_line,
                    end_line=chosen.end_line,
                    content=chosen.content,
                    score=score + add,
                    symbols=chosen.symbols or hit.symbols,
                    layer=chosen.layer or hit.layer,
                    source="hybrid",
                ))
            else:
                fused[key] = (
                    add,
                    ChunkHit(
                        path=hit.path,
                        start_line=hit.start_line,
                        end_line=hit.end_line,
                        content=hit.content,
                        score=add,
                        symbols=hit.symbols,
                        layer=hit.layer,
                        source="hybrid",
                    ),
                )
    return fused


def mmr_select(
    candidates: list[ChunkHit],
    *,
    top_k: int,
    lambda_mult: float = 0.7,
    max_per_path: int = 2,
) -> list[ChunkHit]:
    """Maximal Marginal Relevance with per-path cap for diversity."""
    if not candidates:
        return []
    selected: list[ChunkHit] = []
    path_counts: dict[str, int] = {}
    remaining = list(candidates)

    while remaining and len(selected) < top_k:
        best_idx = -1
        best_score = -1e18
        for i, cand in enumerate(remaining):
            if path_counts.get(cand.path, 0) >= max_per_path:
                continue
            relevance = cand.score
            diversity_pen = 0.0
            for prev in selected:
                if prev.path == cand.path:
                    # overlapping line ranges are highly redundant
                    overlap = not (
                        cand.end_line < prev.start_line
                        or cand.start_line > prev.end_line
                    )
                    diversity_pen = max(diversity_pen, 1.0 if overlap else 0.55)
                elif infer_layer(prev.path) == infer_layer(cand.path):
                    diversity_pen = max(diversity_pen, 0.15)
            mmr = lambda_mult * relevance - (1.0 - lambda_mult) * diversity_pen
            if mmr > best_score:
                best_score = mmr
                best_idx = i
        if best_idx < 0:
            break
        chosen = remaining.pop(best_idx)
        selected.append(chosen)
        path_counts[chosen.path] = path_counts.get(chosen.path, 0) + 1
    return selected


def search(
    query_embedding: list[float],
    *,
    top_k: int = 8,
    db_path: Path = DEFAULT_DB,
    prefer_paths: list[str] | None = None,
    query_text: str = "",
    candidate_pool: int | None = None,
) -> list[ChunkHit]:
    """Hybrid search: dense + FTS, RRF fused, MMR diversified."""
    if not db_path.is_file():
        return []

    conn = connect(db_path)
    prefer = set(prefer_paths or [])
    prefer_layers = {infer_layer(p) for p in prefer}
    for layer in list(prefer_layers):
        prefer_layers.update(LAYER_NEIGHBORS.get(layer, ()))

    pool = candidate_pool or max(top_k * 6, 24)
    ranked: list[list[ChunkHit]] = [
        vector_candidates(
            conn,
            query_embedding,
            prefer=prefer,
            prefer_layers=prefer_layers,
            limit=pool,
        )
    ]
    if query_text:
        ranked.append(
            fts_candidates(
                conn,
                query_text,
                prefer=prefer,
                prefer_layers=prefer_layers,
                limit=pool,
            )
        )
    conn.close()

    fused = rrf_fuse(ranked)
    ordered = sorted(fused.values(), key=lambda item: item[0], reverse=True)
    candidates = [hit for _, hit in ordered]
    return mmr_select(candidates, top_k=top_k)


def advanced_retrieve(
    diff: str,
    changed_paths: list[str],
    *,
    db_path: Path = DEFAULT_DB,
    embed_model: str = DEFAULT_EMBED_MODEL,
    top_k: int = 8,
) -> list[ChunkHit]:
    """Multi-query hybrid retrieval for PR review."""
    if not db_path.is_file():
        return []

    prefer = expand_prefer_paths(changed_paths)
    queries = build_queries(diff, changed_paths)
    per_query_k = max(top_k, 8)
    all_ranked: list[list[ChunkHit]] = []

    for query in queries:
        embedding = embed_text(query, model=embed_model)
        hits = search(
            embedding,
            top_k=per_query_k,
            db_path=db_path,
            prefer_paths=prefer,
            query_text=query,
            candidate_pool=max(top_k * 8, 32),
        )
        all_ranked.append(hits)

    fused = rrf_fuse(all_ranked)
    ordered = sorted(fused.values(), key=lambda item: item[0], reverse=True)
    candidates = [hit for _, hit in ordered]
    return mmr_select(candidates, top_k=top_k, max_per_path=2)


def format_hits(hits: list[ChunkHit]) -> str:
    if not hits:
        return "(no retrieved context — run scripts/pr-review-index.sh first)"
    parts: list[str] = []
    for hit in hits:
        meta = []
        if hit.layer:
            meta.append(f"layer={hit.layer}")
        if hit.symbols:
            meta.append(f"symbols={hit.symbols}")
        if hit.source:
            meta.append(f"via={hit.source}")
        meta_s = (" " + " ".join(meta)) if meta else ""
        parts.append(
            f"### {hit.path}:{hit.start_line}-{hit.end_line} "
            f"(score={hit.score:.3f}{meta_s})\n```\n{hit.content}\n```"
        )
    return "\n\n".join(parts)
