#!/usr/bin/env sh
# Clone or update the Estonian Legal Ontology corpus onto the (persistent)
# volume, then exec the MCP server. LFS is intentionally skipped: estleg reads
# the per-file *_peep.json blobs (regular git objects), never the large LFS
# artifacts (combined_ontology.jsonld / similarity_index.json).
set -eu

CORPUS_DIR="${ESTLEG_CORPUS:-/data/estonian-legal-ontology}"
REPO="${ESTLEG_CORPUS_REPO:-https://github.com/henrikaavik/estonian-legal-ontology.git}"
BRANCH="${ESTLEG_CORPUS_BRANCH:-main}"

export GIT_LFS_SKIP_SMUDGE=1
mkdir -p "$(dirname "$CORPUS_DIR")"

if [ -d "$CORPUS_DIR/.git" ]; then
  echo "[entrypoint] updating corpus in $CORPUS_DIR (branch $BRANCH)"
  git -C "$CORPUS_DIR" fetch --depth 1 origin "$BRANCH" || echo "[entrypoint] fetch failed; serving existing corpus"
  git -C "$CORPUS_DIR" reset --hard "origin/$BRANCH" || true
else
  echo "[entrypoint] cloning corpus -> $CORPUS_DIR (LFS skipped)"
  git clone --depth 1 --branch "$BRANCH" "$REPO" "$CORPUS_DIR"
fi

if [ -f "$CORPUS_DIR/krr_outputs/INDEX.json" ]; then
  echo "[entrypoint] corpus ready (INDEX.json present)"
else
  echo "[entrypoint] WARNING: $CORPUS_DIR/krr_outputs/INDEX.json missing; tools will return empty"
fi

exec estleg-mcp
