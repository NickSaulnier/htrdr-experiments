#!/usr/bin/env bash
# Fetch the htrdr input data that ships with meso-star's repositories into
# $DATA (default ~/htrdr-data), verified by SHA-256.
#
#   bash render-agent/scripts/fetch-data.sh
#
# htgop's etc.tgz holds ecrad_opt_prop.txt, real gas optical properties
# (ecRad k-distribution, shortwave and longwave): enough for clear-sky
# scenes. In the repository it is a Git LFS object, so a plain clone only
# has a 133-byte pointer; GitLab's raw endpoint serves the content.

set -euo pipefail

DATA=${DATA:-$HOME/htrdr-data}
URL=https://gitlab.com/meso-star/htgop/-/raw/0.2/etc.tgz
SHA256=68f33206ad502e4ae5a42c6ef6c1dd08d07680cbf8bf2415c2fe2aad6687cef9

if [ -f "$DATA/etc/ecrad_opt_prop.txt" ] && [ "${FORCE:-0}" != 1 ]; then
  echo "already present: $DATA/etc/ecrad_opt_prop.txt"
  exit 0
fi

mkdir -p "$DATA"
tmp=$(mktemp -d)
trap 'rm -rf "$tmp"' EXIT
curl -sSfL --max-time 300 -o "$tmp/etc.tgz" "$URL"
echo "$SHA256  $tmp/etc.tgz" | sha256sum -c --quiet
tar -xzf "$tmp/etc.tgz" -C "$DATA"
mv "$tmp/etc.tgz" "$DATA/etc.tgz"
echo "gas optical properties: $DATA/etc/ecrad_opt_prop.txt"
