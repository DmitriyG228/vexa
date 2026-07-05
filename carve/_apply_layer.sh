#!/usr/bin/env bash
# carve/_apply_layer.sh — apply the carve-owned override layer in a target dir.
# Shared by seed.sh and sync.sh: copies override files and runs the transform hook.
# (The docs corpus is flat under docs/ and committed — it flows via CARVE_INCLUDE;
# the worktree lay-in below is a retired escape hatch.)
set -euo pipefail
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "$HERE/manifest.sh"
DEST="$1"

# 1. carve-owned override files
for entry in "${CARVE_OVERRIDES[@]}"; do
  src="${entry%%:*}"; dst="${entry##*:}"
  mkdir -p "$DEST/$(dirname "$dst")"
  cp "$HERE/overrides/$src" "$DEST/$dst"
done

# 2. deterministic transforms
[ -x "$CARVE_TRANSFORM" ] && ( cd "$DEST" && "$CARVE_TRANSFORM" )

# 3. docs from worktree (retired escape hatch; corpus is flat under docs/)
if [ "${CARVE_DOCS_FROM_WORKTREE:-0}" = "1" ] && [ -d "$MONO/docs" ]; then
  mkdir -p "$DEST/docs"
  rsync -a --exclude '.git' --exclude '*.log' \
    "$MONO/docs/docs.json" "$MONO/docs/"*.mdx "$MONO/docs/README.md" \
    "$MONO/docs/api" "$MONO/docs/architecture" "$MONO/docs/clients" "$MONO/docs/core" \
    "$MONO/docs/deployment" "$MONO/docs/how-to" "$MONO/docs/roadmap" \
    "$DEST/docs/"
fi
