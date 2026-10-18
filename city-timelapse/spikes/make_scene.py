"""Write the small test scenes the M0 spikes render.

    python3 make_scene.py OUT_DIR [--materials N] [--swap]

OUT_DIR gets:
  plane_box.obj   a 200 m ground with a 20 x 20 x 30 m box at the centre;
  grey.mrumtl     a grey lambertian BRDF over all wavelengths;
  plane_box.mtls  the materials file.

With --materials N the ground is split into a grid of N tiles, each with its
own material name (all pointing at grey.mrumtl, temperatures 290-329 K), to
test how htrdr copes with one material per facet. --swap writes the usemtl
groups as air:material instead of material:air, to find which side htrdr
treats as the front.
"""

import argparse
import math
from pathlib import Path


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("out", type=Path)
    ap.add_argument("--materials", type=int, default=1)
    ap.add_argument("--swap", action="store_true")
    args = ap.parse_args()
    out = args.out.resolve()
    out.mkdir(parents=True, exist_ok=True)

    verts: list[tuple[float, float, float]] = []

    def vid(p):
        verts.append(p)
        return len(verts)

    groups: list[tuple[str, list[tuple[int, int, int]]]] = []

    # Ground tiles. Seen from above (+Z), clockwise is (x0,y0) (x0,y1) (x1,y1) (x1,y0).
    k = math.isqrt(args.materials - 1) + 1
    size = 200 / k
    names = []
    for i in range(k * k):
        if i >= args.materials:
            break
        x0 = -100 + (i % k) * size
        y0 = -100 + (i // k) * size
        a, b, c, d = (vid(p) for p in [(x0, y0, 0), (x0, y0 + size, 0), (x0 + size, y0 + size, 0), (x0 + size, y0, 0)])
        name = f"ground{i:05d}"
        names.append(name)
        groups.append((name, [(a, b, c), (a, c, d)]))
    if len(names) < k * k:
        # Fill the rest of the grid with one last tile per row so the ground has no holes.
        for i in range(len(names), k * k):
            x0 = -100 + (i % k) * size
            y0 = -100 + (i // k) * size
            a, b, c, d = (vid(p) for p in [(x0, y0, 0), (x0, y0 + size, 0), (x0 + size, y0 + size, 0), (x0 + size, y0, 0)])
            groups.append((names[i % len(names)], [(a, b, c), (a, c, d)]))

    # Box faces, wound clockwise when seen from outside.
    x0, x1, y0, y1, z1 = -10, 10, -10, 10, 30
    c = {}
    for x in (x0, x1):
        for y in (y0, y1):
            for z in (0, z1):
                c[(x, y, z)] = vid((x, y, z))
    faces = [
        [c[(x0, y0, z1)], c[(x0, y1, z1)], c[(x1, y1, z1)], c[(x1, y0, z1)]],  # top
        [c[(x0, y0, 0)], c[(x0, y0, z1)], c[(x1, y0, z1)], c[(x1, y0, 0)]],  # south
        [c[(x1, y1, 0)], c[(x1, y1, z1)], c[(x0, y1, z1)], c[(x0, y1, 0)]],  # north
        [c[(x0, y1, 0)], c[(x0, y1, z1)], c[(x0, y0, z1)], c[(x0, y0, 0)]],  # west
        [c[(x1, y0, 0)], c[(x1, y0, z1)], c[(x1, y1, z1)], c[(x1, y1, 0)]],  # east
    ]
    groups.append(("wall", [t for a, b, cc, d in faces for t in ((a, b, cc), (a, cc, d))]))

    lines = [f"v {x} {y} {z}" for x, y, z in verts]
    for mtl, tris in groups:
        lines.append(f"usemtl {'air:' + mtl if args.swap else mtl + ':air'}")
        lines += [f"f {a} {b} {c3}" for a, b, c3 in tris]
    (out / "plane_box.obj").write_text("\n".join(lines) + "\n")

    grey = out / "grey.mrumtl"
    grey.write_text("bands 1\n100 100000 lambertian 0.3\n")
    mtls = ["air none", f"wall {grey} 300"]
    mtls += [f"{n} {grey} {290 + (i % 40)}" for i, n in enumerate(names)]
    (out / "plane_box.mtls").write_text("\n".join(mtls) + "\n")


if __name__ == "__main__":
    main()
