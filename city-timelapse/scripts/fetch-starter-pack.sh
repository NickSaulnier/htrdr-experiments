#!/usr/bin/env bash
# Fetch meso-star's htrdr Atmosphere Starter Pack into $DATA/spk (default
# ~/htrdr-data/spk), verified by SHA-512.
#
#   bash city-timelapse/scripts/fetch-starter-pack.sh
#
# The pack (1.8 GB) holds the SLUM urban materials as mrumtl files, LES cloud
# fields in htcp format and the Mie optics of water droplets. The checksum is
# the one meso-star publishes next to the archive, pinned here so a changed
# file on the server is caught rather than trusted.

set -euo pipefail

DATA=${DATA:-$HOME/htrdr-data}
NAME=htrdr-Atmosphere-Starter-Pack-0.8.0
URL=https://www.meso-star.com/projects/htrdr/downloads/$NAME.tar.gz
SHA512=a490500dfaf78f6239c40c2b805f970a58c91cab771094b087a2d3c9109cd5ab21c6d0ee3bf19910eda5386fd6c2d483933ecca5570e4db5a0c6954cfe1cd976

if [ -d "$DATA/spk/$NAME" ] && [ "${FORCE:-0}" != 1 ]; then
  echo "already present: $DATA/spk/$NAME"
  exit 0
fi

mkdir -p "$DATA/spk"
archive="$DATA/spk/$NAME.tar.gz"
# Resumable: a partial download from an earlier run is continued.
curl -sSfL --retry 3 -C - -o "$archive" "$URL"
echo "$SHA512  $archive" | sha512sum -c --quiet

tmp=$(mktemp -d "$DATA/spk/unpack.XXXXXX")
trap 'rm -rf "$tmp"' EXIT
tar -xzf "$archive" -C "$tmp"
# The archive has a single top-level directory; refuse anything else.
top=$(ls -A "$tmp")
if [ "$(printf '%s\n' "$top" | wc -l)" != 1 ] || [ ! -d "$tmp/$top" ]; then
  echo "unexpected archive layout: $top" >&2
  exit 1
fi
rm -rf "${DATA:?}/spk/$NAME"
mv "$tmp/$top" "$DATA/spk/$NAME"
echo "starter pack: $DATA/spk/$NAME"
