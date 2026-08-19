#!/usr/bin/env python3
"""Optional semantic search over retrieval chunks (#524).

``generate_similarity_index.py`` stays TF-IDF. This module adds a
pluggable embedder + cosine search surface. Tests inject a deterministic
hashing embedder so CI never downloads a model. Production use:

    pip install -e ".[embeddings]"
    python3 -m estleg.generate_embedding_index --model intfloat/multilingual-e5-small

Vectors are written under ``krr_outputs/retrieval/`` and are gitignored
(same policy as ``chunks.jsonl``).
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import sys
from collections.abc import Sequence
from pathlib import Path

from estleg.estleg_common import BUILD_EVALUATION_DATE, KRR_DIR, save_json

RETRIEVAL_DIR = KRR_DIR / "retrieval"
DEFAULT_INDEX_NAME = "embeddings.jsonl"
DEFAULT_META_NAME = "embeddings_manifest.json"
VECTOR_DIM = 64


class Embedder:
    """Minimal encode contract. Implementations must be deterministic."""

    name = "embedder"

    def encode(self, texts: Sequence[str]) -> list[list[float]]:
        raise NotImplementedError


class HashingEmbedder(Embedder):
    """Bag-of-token hashing trick. Offline, no extra deps, test-safe."""

    name = "hashing-v1"

    def __init__(self, dim: int = VECTOR_DIM) -> None:
        self.dim = dim

    def encode(self, texts: Sequence[str]) -> list[list[float]]:
        return [self._one(text) for text in texts]

    def _one(self, text: str) -> list[float]:
        vec = [0.0] * self.dim
        for token in _tokens(text):
            digest = hashlib.sha256(token.encode("utf-8")).digest()
            index = int.from_bytes(digest[:4], "big") % self.dim
            sign = 1.0 if digest[4] % 2 == 0 else -1.0
            vec[index] += sign
        return _l2_normalize(vec)


def sentence_transformer_embedder(model_name: str) -> Embedder:
    """Load multilingual-e5 (or any ST model) when the extra is installed."""
    try:
        from sentence_transformers import SentenceTransformer
    except ImportError as exc:
        raise RuntimeError(
            "sentence-transformers is not installed; "
            'pip install -e ".[embeddings]"'
        ) from exc

    class _ST(Embedder):
        name = model_name

        def __init__(self) -> None:
            self._model = SentenceTransformer(model_name)

        def encode(self, texts: Sequence[str]) -> list[list[float]]:
            matrix = self._model.encode(
                list(texts), normalize_embeddings=True, show_progress_bar=False
            )
            return [list(map(float, row)) for row in matrix]

    return _ST()


def _tokens(text: str) -> list[str]:
    return [part.casefold() for part in text.split() if part.strip()]


def _l2_normalize(vec: Sequence[float]) -> list[float]:
    norm = math.sqrt(sum(value * value for value in vec))
    if norm == 0:
        return [0.0] * len(vec)
    return [value / norm for value in vec]


def cosine(left: Sequence[float], right: Sequence[float]) -> float:
    return sum(a * b for a, b in zip(left, right, strict=True))


def semantic_search(
    query_vec: Sequence[float],
    records: Sequence[dict],
    *,
    top_k: int = 5,
) -> list[dict]:
    """Rank records that already carry a ``vector`` field."""
    scored: list[tuple[float, dict]] = []
    for record in records:
        vector = record.get("vector")
        if not isinstance(vector, list) or not vector:
            continue
        scored.append((cosine(query_vec, vector), record))
    scored.sort(key=lambda item: (-item[0], str(item[1].get("id") or "")))
    hits = []
    for score, record in scored[:top_k]:
        hits.append({
            "id": record.get("id"),
            "score": round(score, 6),
            "text": record.get("text"),
            "rt_url": record.get("rt_url"),
        })
    return hits


def load_chunk_records(path: Path, *, limit: int | None = None) -> list[dict]:
    """Read retrieval chunks.jsonl (or a test fixture) into records."""
    records: list[dict] = []
    if not path.exists():
        return records
    with path.open(encoding="utf-8") as handle:
        for line in handle:
            line = line.strip()
            if not line:
                continue
            item = json.loads(line)
            if not isinstance(item, dict):
                continue
            records.append({
                "id": item.get("provision_iri") or item.get("id"),
                "text": item.get("text") or item.get("summary") or "",
                "rt_url": item.get("rt_url"),
            })
            if limit is not None and len(records) >= limit:
                break
    return records


def build_index(records: list[dict], embedder: Embedder) -> list[dict]:
    vectors = embedder.encode([str(record.get("text") or "") for record in records])
    out = []
    for record, vector in zip(records, vectors, strict=True):
        item = dict(record)
        item["vector"] = vector
        out.append(item)
    return out


def write_index(
    records: list[dict],
    dest: Path,
    *,
    embedder_name: str,
) -> Path:
    dest.parent.mkdir(parents=True, exist_ok=True)
    with dest.open("w", encoding="utf-8") as handle:
        for record in records:
            handle.write(json.dumps(record, ensure_ascii=False) + "\n")
    save_json(
        dest.with_name(DEFAULT_META_NAME),
        {
            "generated": BUILD_EVALUATION_DATE,
            "embedder": embedder_name,
            "records": len(records),
            "dim": len(records[0]["vector"]) if records else 0,
        },
    )
    return dest


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--chunks",
        type=Path,
        default=RETRIEVAL_DIR / "chunks.sample.jsonl",
        help="Input JSONL (default: committed sample, not the gitignored full dump)",
    )
    parser.add_argument(
        "--out",
        type=Path,
        default=RETRIEVAL_DIR / DEFAULT_INDEX_NAME,
        help="Output embeddings JSONL (gitignored)",
    )
    parser.add_argument(
        "--model",
        default="",
        help="sentence-transformers model; empty uses the hashing embedder",
    )
    parser.add_argument("--limit", type=int, default=None)
    parser.add_argument("--query", default="", help="Optional search string")
    parser.add_argument("--top-k", type=int, default=5)
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args([] if argv is None else argv)
    embedder: Embedder
    if args.model:
        embedder = sentence_transformer_embedder(args.model)
    else:
        embedder = HashingEmbedder()
    records = load_chunk_records(args.chunks, limit=args.limit)
    indexed = build_index(records, embedder)
    write_index(indexed, args.out, embedder_name=embedder.name)
    print(f"wrote {len(indexed)} vectors via {embedder.name} → {args.out}")
    if args.query:
        query_vec = embedder.encode([args.query])[0]
        for hit in semantic_search(query_vec, indexed, top_k=args.top_k):
            print(f"  {hit['score']:.3f}  {hit['id']}  {hit.get('rt_url') or ''}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
