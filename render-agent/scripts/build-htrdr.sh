#!/usr/bin/env bash
# Build htrdr-atmosphere and its whole dependency stack into $PREFIX, on
# Ubuntu 22.04 (WSL included), without root.
#
#   bash render-agent/scripts/build-htrdr.sh
#
# Needs these packages first (the only step that needs sudo):
#   sudo apt-get install -y build-essential pkg-config git libopenmpi-dev \
#     openmpi-bin libnetcdf-dev netcdf-bin librandom123-dev libtbb-dev cmake mandoc
#
# Built from source because Ubuntu 22.04's packages are too old:
#   SLEEF >= 3.6 (apt has 3.5.1) for RSIMD, and Embree 4 (apt has 3.12) for
#   Star-3D. Everything else comes from meso-star's repositories, pinned to
#   the tags below. Safe to rerun: finished steps are skipped unless FORCE=1.
#
# Afterwards, run htrdr with:
#   LD_LIBRARY_PATH=$PREFIX/lib $PREFIX/bin/htrdr-atmosphere ...

set -euo pipefail

PREFIX=${PREFIX:-$HOME/.local}
SRC=${SRC:-$HOME/src}
JOBS=${JOBS:-$(nproc)}
FORCE=${FORCE:-0}

export PKG_CONFIG_PATH="$PREFIX/lib/pkgconfig:$PREFIX/lib64/pkgconfig:$PREFIX/share/pkgconfig${PKG_CONFIG_PATH:+:$PKG_CONFIG_PATH}"
export LD_LIBRARY_PATH="$PREFIX/lib:$PREFIX/lib64${LD_LIBRARY_PATH:+:$LD_LIBRARY_PATH}"

SLEEF_TAG=3.6.1
EMBREE_TAG=v4.3.3

# name  repository                    tag     extra make arguments
MESO_STAR=(
  "rsys        https://gitlab.com/vaplv/rsys.git            0.15"
  "star-sp     https://gitlab.com/meso-star/star-sp.git     0.15"
  "rsimd       https://gitlab.com/vaplv/rsimd.git           0.6.1"
  "star-sf     https://gitlab.com/meso-star/star-sf.git     0.10"
  "star-3d     https://gitlab.com/meso-star/star-3d.git     0.10.1"
  "star-camera https://gitlab.com/meso-star/star-camera.git 0.2"
  "star-vx     https://gitlab.com/meso-star/star-vx.git     0.3.1"
  "loader_aw   https://gitlab.com/vaplv/loader_aw.git       2.1"
  "mrumtl      https://gitlab.com/meso-star/mrumtl.git      0.2"
  "htgop       https://gitlab.com/meso-star/htgop.git       0.2"
  "htcp        https://gitlab.com/meso-star/htcp.git        0.2"
  "htmie       https://gitlab.com/meso-star/htmie.git       0.1"
  "htsky       https://gitlab.com/meso-star/htsky.git       0.3.1"
  "htrdr       https://gitlab.com/meso-star/htrdr.git       0.12    COMBUSTION=DISABLE PLANETS=DISABLE"
)

log() { printf '\n=== %s\n' "$*"; }
done_marker() { echo "$SRC/.built-$1"; }
is_done() { [ "$FORCE" != 1 ] && [ -f "$(done_marker "$1")" ]; }
mark_done() { touch "$(done_marker "$1")"; }

# Clone `url` into $SRC/name (if needed) and check out `tag`.
fetch() {
  local name=$1 url=$2 tag=$3
  if [ ! -d "$SRC/$name/.git" ]; then
    git clone -q "$url" "$SRC/$name"
  fi
  git -C "$SRC/$name" fetch -q --tags
  git -C "$SRC/$name" -c advice.detachedHead=false checkout -q "$tag"
}

# A pkg-config file for libraries whose packages or builds don't ship one.
write_pc() {
  local name=$1 version=$2 cflags=$3 libs=$4
  mkdir -p "$PREFIX/lib/pkgconfig"
  cat >"$PREFIX/lib/pkgconfig/$name.pc" <<EOF
Name: $name
Description: $name (pkg-config file written by build-htrdr.sh)
Version: $version
Cflags: $cflags
Libs: $libs
EOF
}

mkdir -p "$SRC" "$PREFIX"

# Random123 is header-only; Debian's package has no pkg-config file.
if ! pkg-config --exists random123; then
  log "random123: writing a pkg-config file for the system headers"
  [ -f /usr/include/Random123/philox.h ] || { echo "install librandom123-dev first" >&2; exit 1; }
  write_pc random123 1.14.0 "" ""
fi

if ! is_done sleef; then
  log "SLEEF $SLEEF_TAG"
  fetch sleef https://github.com/shibatch/sleef.git "$SLEEF_TAG"
  cmake -S "$SRC/sleef" -B "$SRC/sleef/build" -DCMAKE_BUILD_TYPE=Release \
    -DCMAKE_INSTALL_PREFIX="$PREFIX" -DCMAKE_INSTALL_LIBDIR=lib -DBUILD_SHARED_LIBS=ON \
    -DSLEEF_BUILD_TESTS=OFF -DSLEEF_BUILD_DFT=OFF -DSLEEF_BUILD_QUAD=OFF -DSLEEF_BUILD_GNUABI_LIBS=OFF >/dev/null
  cmake --build "$SRC/sleef/build" -j "$JOBS" >/dev/null
  cmake --install "$SRC/sleef/build" >/dev/null
  pkg-config --exists sleef || write_pc sleef "${SLEEF_TAG}" "-I$PREFIX/include" "-L$PREFIX/lib -lsleef"
  mark_done sleef
fi

if ! is_done embree; then
  log "Embree $EMBREE_TAG"
  fetch embree https://github.com/RenderKit/embree.git "$EMBREE_TAG"
  cmake -S "$SRC/embree" -B "$SRC/embree/build" -DCMAKE_BUILD_TYPE=Release \
    -DCMAKE_INSTALL_PREFIX="$PREFIX" -DCMAKE_INSTALL_LIBDIR=lib \
    -DEMBREE_ISPC_SUPPORT=OFF -DEMBREE_TUTORIALS=OFF -DEMBREE_TASKING_SYSTEM=TBB >/dev/null
  cmake --build "$SRC/embree/build" -j "$JOBS" >/dev/null
  cmake --install "$SRC/embree/build" >/dev/null
  pkg-config --exists embree4 || write_pc embree4 "${EMBREE_TAG#v}" "-I$PREFIX/include" "-L$PREFIX/lib -lembree4"
  mark_done embree
fi

for entry in "${MESO_STAR[@]}"; do
  read -r name url tag extra <<<"$entry"
  is_done "$name" && continue
  log "$name $tag"
  fetch "$name" "$url" "$tag"
  # shellcheck disable=SC2086  # $extra is a list of make variables
  make -C "$SRC/$name" clean >/dev/null 2>&1 || true
  make -C "$SRC/$name" -j "$JOBS" PREFIX="$PREFIX" $extra >"$SRC/$name/build.log" 2>&1 \
    || { tail -30 "$SRC/$name/build.log"; exit 1; }
  make -C "$SRC/$name" install PREFIX="$PREFIX" $extra >"$SRC/$name/install.log" 2>&1 \
    || { tail -30 "$SRC/$name/install.log"; exit 1; }
  mark_done "$name"
done

log "done"
"$PREFIX/bin/htrdr-atmosphere" -h 2>&1 | head -3 || true
