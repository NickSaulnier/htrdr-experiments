#!/usr/bin/env bash
# The M0 spikes, as run. Results are recorded in ../PLAN.md (M0 findings).
#
#   bash city-timelapse/spikes/m0.sh [spike ...]     # default: all
#
# Spikes: orient materials flux timing slum clouds cache. Outputs go to $OUT
# (default ~/citylapse-spikes). The cloud spikes need the starter pack
# (scripts/fetch-starter-pack.sh).

set -uo pipefail

HERE=$(cd "$(dirname "$0")" && pwd)
OUT=${OUT:-$HOME/citylapse-spikes}
GAS=${GAS:-$HOME/htrdr-data/etc/ecrad_opt_prop.txt}
SPK=${SPK:-$HOME/htrdr-data/spk/htrdr-Atmosphere-Starter-Pack-0.8.0}
export LD_LIBRARY_PATH=$HOME/.local/lib${LD_LIBRARY_PATH:+:$LD_LIBRARY_PATH}
HTRDR=${HTRDR:-htrdr-atmosphere}

timed() { /usr/bin/time -f "  %e s wall, %M KB max RSS" "$@"; }

# Mean of component K over the pixels of an htrdr-image, and over a pixel box.
img_mean() {
  python3 - "$@" <<'P'
import sys
f, k = sys.argv[1], int(sys.argv[2])
rows = [l.split() for l in open(f) if l.strip() and not l.startswith("#")]
w, h = map(int, rows[0][:2])
px = [list(map(float, r)) for r in rows[1:]]
box = list(map(int, sys.argv[3:7])) if len(sys.argv) > 3 else [0, 0, w, h]
sel = [px[y * w + x][k] for y in range(box[1], box[3]) for x in range(box[0], box[2])]
print(f"  {f.rsplit('/', 1)[-1]} comp {k} box {box}: mean {sum(sel) / len(sel):.4g}")
P
}

orient() {
  # Which side of a usemtl front:back pair does a camera above a quad that is
  # clockwise seen from above actually see?
  local d=$OUT/orient
  python3 "$HERE/make_scene.py" "$d"
  printf 'bands 1\n100 100000 lambertian 0.9\n' >"$d/white.mrumtl"
  printf 'bands 1\n100 100000 lambertian 0.02\n' >"$d/black.mrumtl"
  printf 'air none\nwall %s 300\nwhite %s 300\nblack %s 300\n' "$d/grey.mrumtl" "$d/white.mrumtl" "$d/black.mrumtl" >"$d/wb.mtls"
  for pair in white:black black:white white:air:black; do
    sed "s/^usemtl ground00000:air/usemtl $pair/" "$d/plane_box.obj" >"$d/wb.obj"
    $HTRDR -a "$GAS" -g "$d/wb.obj" -M "$d/wb.mtls" -D 135,40 \
      -C pos=-80,-80,40:tgt=0,0,10:up=0,0,1:fov=50 -i def=96x72:spp=16 -f -o "$d/wb-$pair.txt"
    echo "usemtl $pair:"
    img_mean "$d/wb-$pair.txt" 2 43 67 53 72
  done
}

materials() {
  # One material per ground tile, 1 to 20,000 names, longwave, seen from above.
  for n in 1 100 5000 20000; do
    for swap in "" --swap; do
      local d=$OUT/mtl-$n$swap
      python3 "$HERE/make_scene.py" "$d" --materials "$n" $swap
      echo "materials=$n $swap"
      timed $HTRDR -a "$GAS" -g "$d/plane_box.obj" -M "$d/plane_box.mtls" \
        -P pos=0,0,500:tgt=0,0,0:up=0,1,0:height=200 -i def=100x100:spp=64 \
        -s lw=9000,10000 -f -o "$d/lw.txt"
      img_mean "$d/lw.txt" 0
    done
  done
}

flux() {
  # Upward-looking flux sensor at 1 m over the box scene.
  local d=$OUT/mtl-1
  [ -f "$d/plane_box.obj" ] || python3 "$HERE/make_scene.py" "$d"
  for spec in sw=300,4000 lw=4000,100000; do
    echo "flux $spec"
    timed $HTRDR -a "$GAS" -g "$d/plane_box.obj" -M "$d/plane_box.mtls" -D 135,40 \
      -p pos=0,0,1:tgt=0,0,2:up=0,1,0:sz=200,200 -i def=100x100:spp=64 -s "$spec" \
      -f -o "$d/flux-${spec%%=*}.txt"
    img_mean "$d/flux-${spec%%=*}.txt" 0 46 46 54 54   # inside the box footprint
  done
}

timing() {
  # A 960x540 frame of the box scene with an infinite ground, 64 spp.
  local d=$OUT/mtl-1
  [ -f "$d/plane_box.obj" ] || python3 "$HERE/make_scene.py" "$d"
  for spec in cie_xyz lw=9000,10000; do
    echo "timing $spec"
    timed $HTRDR -a "$GAS" -R -g "$d/plane_box.obj" -M "$d/plane_box.mtls" -D 135,40 \
      -C pos=-80,-80,40:tgt=0,0,10:up=0,0,1:fov=50 -i def=960x540:spp=64 -s "$spec" \
      -f -o "$d/big-${spec%%[=_]*}.txt"
  done
}

clouds() {
  # Dump the cloud octrees (-d) and split them into one VTK file each.
  local d=$OUT/clouds htcp mie
  mkdir -p "$d"
  htcp=$(find "$SPK" -name 'DZVAR*.htcp' | head -1)
  mie=$(find "$SPK" -name 'Mie*.nc' | head -1)
  python3 "$HERE/htcp_info.py" "$htcp"
  timed $HTRDR -a "$GAS" -m "$mie" -c "$htcp" -d -f -o "$d/octrees.txt"
  local n
  n=$(grep -c '^# vtk' "$d/octrees.txt")
  echo "  $n octrees, $(du -h "$d/octrees.txt" | cut -f1)"
  rm -f "$d"/octree[0-9]*
  sed '/^---$/d' "$d/octrees.txt" | (cd "$d" && csplit -s -f octree -n 3 -k - '%^# vtk%' '/^# vtk/' "{$((n - 2))}")
  ls "$d" | grep -c '^octree[0-9]'
  # The dump covers the spectral domain of -s (default: visible); 9-10 um too.
  timed $HTRDR -a "$GAS" -m "$mie" -c "$htcp" -s lw=9000,10000 -d -f -o "$d/octrees-lw.txt"
  grep -A1 '^# vtk' "$d/octrees-lw.txt" | grep -v -e '^# vtk' -e '^--' | sort | uniq -c
}

slum() {
  # Visible albedo, 9-10 um emissivity and spectral coverage of the SLUM files.
  python3 "$HERE/slum_table.py" "$SPK/materials/slum"
}

cache() {
  # Build the -O cache with clouds, then reuse it.
  local d=$OUT/clouds htcp mie
  mkdir -p "$d"
  htcp=$(find "$SPK" -name 'DZVAR*.htcp' | head -1)
  mie=$(find "$SPK" -name 'Mie*.nc' | head -1)
  rm -f "$d/cache"
  for run in build reuse; do
    echo "cache $run"
    timed $HTRDR -a "$GAS" -m "$mie" -c "$htcp" -r -O "$d/cache" -D 135,40 \
      -C pos=0,0,200:tgt=1000,1000,1500:up=0,0,1:fov=60 -i def=160x90:spp=16 \
      -f -o "$d/cache-$run.txt"
  done
  ls -la "$d/cache" 2>/dev/null || echo "  no cache file written"
}

for s in "${@:-orient materials flux timing slum clouds cache}"; do
  for t in $s; do "$t"; done
done
