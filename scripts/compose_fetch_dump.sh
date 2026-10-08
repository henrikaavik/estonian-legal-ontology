#!/bin/sh
# Resolve the named-graph dump for `docker compose up` (#716).
#
# Runs inside the `dump` service (alpine, busybox sh). Picks the first of:
#   1. ESTLEG_DUMP           a host .nq.gz the user pointed at (mounted at
#                            /override/dump.nq.gz; ESTLEG_DUMP_OVERRIDE=1)
#   2. release/estleg_all.nq.gz   built locally by build_release_assets.py
#                            (verified against release/SHA256SUMS when present)
#   3. the tagged GitHub release asset v$ESTLEG_VERSION, downloaded with its
#                            SHA256SUMS and verified (cached in the volume)
# and decompresses it to $OUT_DIR/dump.nq for the oxigraph `load` service.
# The 14-quad fixture is not a fallback: it is `--profile sample` only.
set -eu

ASSET=estleg_all.nq.gz
SUMS=SHA256SUMS
OUT_DIR=${OUT_DIR:-/out}
RELEASE_DIR=${RELEASE_DIR:-/release}
OVERRIDE_FILE=${OVERRIDE_FILE:-/override/dump.nq.gz}
VERSION=${ESTLEG_VERSION:?ESTLEG_VERSION must be set}
REPO=${ESTLEG_REPO:-henrikaavik/estonian-legal-ontology}
BASE_URL=${ESTLEG_RELEASE_BASE_URL:-https://github.com/$REPO/releases/download/v$VERSION}
CACHE_DIR=$OUT_DIR/cache

log() { echo "estleg-dump: $*"; }
die() { echo "estleg-dump: error: $*" >&2; exit 1; }

# expected_sha <SHA256SUMS> -> the checksum listed for $ASSET (or nothing)
expected_sha() {
    awk -v name="$ASSET" '$2 == name || $2 == "*" name { print $1; exit }' "$1"
}

# verify <asset file> <SHA256SUMS file>
verify() {
    expected=$(expected_sha "$2")
    [ -n "$expected" ] || die "$ASSET is not listed in $2"
    actual=$(sha256sum "$1" | cut -d' ' -f1)
    [ "$actual" = "$expected" ] || die "SHA256 mismatch for $1 (expected $expected, got $actual)"
    log "sha256 ok ($actual)"
}

fetch() {  # fetch <url> <dest>
    wget -q -O "$2.part" "$1" || { rm -f "$2.part"; die "download failed: $1"; }
    mv "$2.part" "$2"
}

if [ "${ESTLEG_DUMP_OVERRIDE:-}" = "1" ]; then
    src=$OVERRIDE_FILE
    log "using ESTLEG_DUMP override"
elif [ -f "$RELEASE_DIR/$ASSET" ]; then
    src=$RELEASE_DIR/$ASSET
    if [ -f "$RELEASE_DIR/$SUMS" ]; then
        verify "$src" "$RELEASE_DIR/$SUMS"
    else
        log "warning: $RELEASE_DIR/$SUMS missing; local dump not verified"
    fi
    log "using local release/$ASSET"
else
    mkdir -p "$CACHE_DIR"
    log "no local release/$ASSET; fetching v$VERSION from $BASE_URL"
    fetch "$BASE_URL/$SUMS" "$CACHE_DIR/$SUMS"
    src=$CACHE_DIR/$ASSET
    if [ -f "$src" ] && [ "$(sha256sum "$src" | cut -d' ' -f1)" = "$(expected_sha "$CACHE_DIR/$SUMS")" ]; then
        log "cached $ASSET is current"
    else
        fetch "$BASE_URL/$ASSET" "$src"
    fi
    verify "$src" "$CACHE_DIR/$SUMS"
fi

[ -f "$src" ] || die "dump not found: $src"
gzip -dc "$src" > "$OUT_DIR/dump.nq.tmp"
mv "$OUT_DIR/dump.nq.tmp" "$OUT_DIR/dump.nq"
log "wrote $OUT_DIR/dump.nq ($(( $(wc -l < "$OUT_DIR/dump.nq") )) quads)"
