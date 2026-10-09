#!/usr/bin/env sh
# Check out the Estonian Legal Ontology corpus at a pinned release onto the
# (persistent) volume, then exec the MCP server.
#
# * ESTLEG_CORPUS_REF (default v1.0.0) is the git ref served -- a release tag,
#   so every answer is reproducible against a published corpus (#714). A
#   branch name also works but is not reproducible; ESTLEG_CORPUS_BRANCH is
#   still honoured as a deprecated alias when ESTLEG_CORPUS_REF is unset.
# * The checked-out commit is exported as ESTLEG_CORPUS_COMMIT and the ref as
#   ESTLEG_CORPUS_REF, which the server stamps on every result's `snapshot`
#   and every audit line.
# * LFS is skipped: estleg reads the per-file *_peep.json / sidecar blobs
#   (regular git objects), never the large LFS artifacts.
set -eu

CORPUS_DIR="${ESTLEG_CORPUS:-/data/estonian-legal-ontology}"
REPO="${ESTLEG_CORPUS_REPO:-https://github.com/henrikaavik/estonian-legal-ontology.git}"
if [ -z "${ESTLEG_CORPUS_REF:-}" ] && [ -n "${ESTLEG_CORPUS_BRANCH:-}" ]; then
  echo "[entrypoint] ESTLEG_CORPUS_BRANCH is deprecated; use ESTLEG_CORPUS_REF (a release tag)"
  REF="$ESTLEG_CORPUS_BRANCH"
else
  REF="${ESTLEG_CORPUS_REF:-v1.0.0}"
fi

export GIT_LFS_SKIP_SMUDGE=1
DATA_ROOT="$(dirname "$CORPUS_DIR")"
mkdir -p "$DATA_ROOT" 2>/dev/null || true
if [ ! -w "$DATA_ROOT" ]; then
  echo "[entrypoint] ERROR: $DATA_ROOT is not writable by uid $(id -u)." >&2
  echo "[entrypoint] The image runs as the non-root user estleg (10001). Fix the volume once:" >&2
  echo "[entrypoint]   docker run --rm -u 0 -v <volume>:/data <image> chown -R 10001:10001 /data" >&2
  exit 1
fi
# A clone made by an older root-run image is owned by another uid; let git
# operate on it (ownership is fixed by the chown above, this covers the gap).
git config --global --add safe.directory "$CORPUS_DIR" 2>/dev/null || true

if [ -d "$CORPUS_DIR/.git" ]; then
  echo "[entrypoint] updating corpus in $CORPUS_DIR to $REF"
  if git -C "$CORPUS_DIR" fetch --depth 1 --force origin "+refs/tags/$REF:refs/tags/$REF" 2>/dev/null \
     || git -C "$CORPUS_DIR" fetch --depth 1 origin "$REF"; then
    git -C "$CORPUS_DIR" -c advice.detachedHead=false checkout --force --detach FETCH_HEAD
  else
    echo "[entrypoint] ERROR: fetch of $REF failed; refusing to serve a different release" >&2
    exit 1
  fi
else
  echo "[entrypoint] cloning corpus $REF -> $CORPUS_DIR (LFS skipped)"
  git -c advice.detachedHead=false clone --depth 1 --branch "$REF" "$REPO" "$CORPUS_DIR"
fi

ESTLEG_CORPUS_COMMIT="$(git -C "$CORPUS_DIR" rev-parse HEAD 2>/dev/null || echo "")"
ESTLEG_CORPUS_REF="$REF"
export ESTLEG_CORPUS_COMMIT ESTLEG_CORPUS_REF
echo "[entrypoint] corpus at $REF ($ESTLEG_CORPUS_COMMIT)"

if [ -f "$CORPUS_DIR/krr_outputs/INDEX.json" ]; then
  echo "[entrypoint] corpus ready (INDEX.json present)"
else
  echo "[entrypoint] WARNING: $CORPUS_DIR/krr_outputs/INDEX.json missing; tools will return empty"
fi

exec estleg-mcp
