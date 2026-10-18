"""Procedural city tile: geometry in htrdr-obj form plus a facet table.

The tile is a square of `blocks` x `blocks` city blocks with streets centred
on the block lines, including the tile edges, so `-R` repeats it without
seams. Everything sits on an integer lattice: one horizontal unit is `cell`
metres and one vertical unit is a `floor` of `floor_height` metres. Shared
vertices are therefore exact, the mesh is conforming, and the tests can check
that the surface is closed and consistently oriented.

A *facet* is the unit that gets its own material name, and so (in M3) its own
temperature: a ground patch, a roof patch, or one floor of a wall panel. A
facet is made of one or more lattice quads.

Orientation follows the starter pack's city.obj: every quad is wound
counter-clockwise seen from the air (its right-hand normal points into the
air) and its group is written `usemtl <name>:air`. In htrdr-obj terms the
front side, where the vertices look clockwise, is the solid, so the front
medium is the material and the back medium is the air.
"""

from __future__ import annotations

import argparse
import csv
import json
import math
from dataclasses import asdict, dataclass, field
from pathlib import Path

import numpy as np

# Material codes are "<library>/<file stem>" under the starter pack's
# materials/ directory. The SLUM prefixes are used the way meso-star's own
# city.obj uses them: B, C and S codes on walls, R, G and Z codes on roofs, A
# codes on streets (see PLAN.md, M1 findings).
STREET = "slum/A003"
SIDEWALK = "slum/C004"
YARD = ("slum/S002", "slum/G002", "slum/C001")
PARK = "legacy/grass"
WALLS = ("slum/B001", "slum/B002", "slum/B005", "slum/B007", "slum/B008", "slum/B011",
         "slum/B013", "slum/C003", "slum/C006", "slum/C008", "slum/S004", "slum/S005")
ROOFS = ("slum/R001", "slum/R002", "slum/R004", "slum/R009", "slum/R012",
         "slum/G001", "slum/G004", "slum/Z001", "slum/A008")

Key = tuple[int, int, int]  # lattice vertex: (i, j) in cells, k in floors


@dataclass(frozen=True)
class CityConfig:
    seed: int = 1
    blocks: int = 3            # blocks per side
    block_cells: int = 40      # block pitch, in cells
    street_cells: int = 3      # half street width, in cells
    sidewalk_cells: int = 2    # sidewalk width, in cells
    cell: float = 2.0          # m
    floor_height: float = 3.0  # m
    park_blocks: int = 1
    towers: int = 2
    building_prob: float = 0.85
    median_floors: float = 6.0
    floors_sigma: float = 0.45  # of log(floors)
    min_floors: int = 2
    max_floors: int = 15
    tower_floors: tuple[int, int] = (18, 28)
    ground_patch: int = 1      # cells per ground facet side
    roof_patch: int = 2        # cells per roof facet side
    wall_patch: int = 2        # cells per wall facet width (one floor tall)

    @property
    def tile_cells(self) -> int:
        return self.blocks * self.block_cells

    @property
    def tile_size(self) -> float:
        return self.tile_cells * self.cell


@dataclass
class Building:
    id: int
    block: tuple[int, int]
    x0: int
    y0: int
    x1: int  # exclusive, in cells
    y1: int
    floors: int
    wall: str
    roof: str
    tower: bool = False

    def covers(self, i: int, j: int) -> bool:
        return self.x0 <= i < self.x1 and self.y0 <= j < self.y1


@dataclass
class Facet:
    id: int
    kind: str          # street, sidewalk, yard, park, roof, wall
    material: str
    normal: tuple[int, int, int]
    building: int = -1
    quads: list[tuple[Key, Key, Key, Key]] = field(default_factory=list)

    @property
    def name(self) -> str:
        return f"{self.kind}{self.id:05d}"


@dataclass
class City:
    config: CityConfig
    buildings: list[Building]
    facets: list[Facet]

    def position(self, key: Key) -> tuple[float, float, float]:
        c = self.config
        return key[0] * c.cell, key[1] * c.cell, key[2] * c.floor_height

    def triangles(self):
        """Yield (facet, (a, b, c)) for every triangle, as lattice keys."""
        for f in self.facets:
            for a, b, cc, d in f.quads:
                yield f, (a, b, cc)
                yield f, (a, cc, d)


def oriented(quad: tuple[Key, Key, Key, Key], normal: tuple[int, int, int], c: CityConfig):
    """Return the quad in the cyclic order whose right-hand normal is `normal`."""
    p = [np.array([k[0] * c.cell, k[1] * c.cell, k[2] * c.floor_height]) for k in quad]
    n = np.cross(p[1] - p[0], p[2] - p[0])
    return quad if np.dot(n, normal) > 0 else (quad[0], quad[3], quad[2], quad[1])


def ground_kind(c: CityConfig, i: int, j: int) -> str:
    """Street, sidewalk or block interior for ground cell (i, j)."""
    def band(u: int) -> int:
        u %= c.block_cells
        d = min(u, c.block_cells - 1 - u)  # cells from the nearest block line
        return 0 if d < c.street_cells else 1 if d < c.street_cells + c.sidewalk_cells else 2

    b = min(band(i), band(j))
    return ("street", "sidewalk", "interior")[b]


def split(rng: np.random.Generator, lo: int, hi: int, min_size: int) -> list[tuple[int, int]]:
    """Split [lo, hi) into 2 or 3 parts of at least min_size cells."""
    n = int(rng.integers(2, 4))
    while n > 1 and (hi - lo) < n * min_size:
        n -= 1
    cuts = [lo]
    for k in range(1, n):
        left = cuts[-1] + min_size
        right = hi - (n - k) * min_size
        cuts.append(int(rng.integers(left, right + 1)))
    cuts.append(hi)
    return list(zip(cuts, cuts[1:]))


def layout(c: CityConfig, rng: np.random.Generator) -> tuple[list[Building], set[tuple[int, int]]]:
    """Place buildings in each block; return them and the set of park blocks."""
    inner0 = c.street_cells + c.sidewalk_cells
    inner1 = c.block_cells - inner0
    blocks = [(bi, bj) for bj in range(c.blocks) for bi in range(c.blocks)]
    park = {blocks[k] for k in rng.choice(len(blocks), size=c.park_blocks, replace=False)}

    lots = []
    for bi, bj in blocks:
        if (bi, bj) in park:
            continue
        ox, oy = bi * c.block_cells, bj * c.block_cells
        for x0, x1 in split(rng, inner0, inner1, 8):
            for y0, y1 in split(rng, inner0, inner1, 8):
                lots.append(((bi, bj), ox + x0, ox + x1, oy + y0, oy + y1,
                             x0 == inner0, x1 == inner1, y0 == inner0, y1 == inner1))

    buildings = []
    for block, x0, x1, y0, y1, w, e, s, n in lots:
        if rng.random() > c.building_prob:
            continue
        # Street-facing sides are usually built to the sidewalk; interior sides
        # step back one cell so neighbours never share a wall.
        def back(street: bool) -> int:
            return int(rng.random() < 0.3) if street else 1
        bx0, bx1 = x0 + back(w), x1 - back(e)
        by0, by1 = y0 + back(s), y1 - back(n)
        floors = int(round(math.exp(math.log(c.median_floors) + c.floors_sigma * rng.standard_normal())))
        floors = min(max(floors, c.min_floors), c.max_floors)
        buildings.append(Building(len(buildings), block, bx0, by0, bx1, by1, floors,
                                  str(rng.choice(WALLS)), str(rng.choice(ROOFS))))

    # The largest footprints become towers.
    for b in sorted(buildings, key=lambda b: (b.x1 - b.x0) * (b.y1 - b.y0), reverse=True)[:c.towers]:
        b.floors = int(rng.integers(c.tower_floors[0], c.tower_floors[1] + 1))
        b.tower = True
    return buildings, park


def generate(c: CityConfig) -> City:
    rng = np.random.default_rng(c.seed)
    buildings, park = layout(c, rng)
    n = c.tile_cells
    owner = np.full((n, n), -1, dtype=np.int32)
    for b in buildings:
        owner[b.x0:b.x1, b.y0:b.y1] = b.id

    facets: list[Facet] = []

    def facet(kind: str, material: str, normal, building: int = -1) -> Facet:
        f = Facet(len(facets), kind, material, normal, building)
        facets.append(f)
        return f

    # Ground: one facet per ground_patch x ground_patch cells of the same kind.
    yard_material = {blk: str(rng.choice(YARD)) for blk in
                     [(bi, bj) for bj in range(c.blocks) for bi in range(c.blocks)]}
    up = (0, 0, 1)
    patches: dict[tuple, Facet] = {}
    p = c.ground_patch
    for j in range(n):
        for i in range(n):
            if owner[i, j] >= 0:
                continue
            kind = ground_kind(c, i, j)
            block = (i // c.block_cells, j // c.block_cells)
            if kind == "interior":
                kind, material = ("park", PARK) if block in park else ("yard", yard_material[block])
            else:
                material = STREET if kind == "street" else SIDEWALK
            key = (kind, material, i // p, j // p)
            f = patches.get(key) or patches.setdefault(key, facet(kind, material, up))
            f.quads.append(oriented(((i, j, 0), (i + 1, j, 0), (i + 1, j + 1, 0), (i, j + 1, 0)), up, c))

    for b in buildings:
        # Roof patches, aligned on the footprint's corner.
        rp = c.roof_patch
        roof: dict[tuple[int, int], Facet] = {}
        for j in range(b.y0, b.y1):
            for i in range(b.x0, b.x1):
                key = ((i - b.x0) // rp, (j - b.y0) // rp)
                f = roof.get(key) or roof.setdefault(key, facet("roof", b.roof, up, b.id))
                k = b.floors
                f.quads.append(oriented(((i, j, k), (i + 1, j, k), (i + 1, j + 1, k), (i, j + 1, k)), up, c))
        # Walls: per side, columns of one cell, grouped wall_patch wide and one floor tall.
        sides = [
            ((0, -1, 0), [((i, b.y0), (i + 1, b.y0)) for i in range(b.x0, b.x1)]),
            ((0, 1, 0), [((i, b.y1), (i + 1, b.y1)) for i in range(b.x0, b.x1)]),
            ((-1, 0, 0), [((b.x0, j), (b.x0, j + 1)) for j in range(b.y0, b.y1)]),
            ((1, 0, 0), [((b.x1, j), (b.x1, j + 1)) for j in range(b.y0, b.y1)]),
        ]
        for normal, columns in sides:
            for k in range(b.floors):
                f = None
                for col, ((xa, ya), (xb, yb)) in enumerate(columns):
                    if col % c.wall_patch == 0:
                        f = facet("wall", b.wall, normal, b.id)
                    f.quads.append(oriented(((xa, ya, k), (xb, yb, k), (xb, yb, k + 1), (xa, ya, k + 1)), normal, c))
    return City(c, buildings, facets)


def write_obj(city: City, path: Path) -> None:
    index: dict[Key, int] = {}
    vlines: list[str] = []
    glines: list[str] = []

    def vid(k: Key) -> int:
        if k not in index:
            index[k] = len(index) + 1
            x, y, z = city.position(k)
            vlines.append(f"v {x:g} {y:g} {z:g}")
        return index[k]

    for f in city.facets:
        glines.append(f"usemtl {f.name}:air")
        for a, b, cc, d in f.quads:
            ia, ib, ic, i_d = vid(a), vid(b), vid(cc), vid(d)
            glines.append(f"f {ia} {ib} {ic}")
            glines.append(f"f {ia} {ic} {i_d}")
    path.write_text("\n".join(vlines + glines) + "\n")


def facet_rows(city: City) -> list[dict]:
    c = city.config
    rows = []
    for f in city.facets:
        pts = np.array([[city.position(k) for k in q] for q in f.quads])  # (nq, 4, 3)
        e1 = pts[:, 1] - pts[:, 0]
        e2 = pts[:, 3] - pts[:, 0]
        area = float(np.linalg.norm(np.cross(e1, e2), axis=1).sum())
        centroid = pts.reshape(-1, 3).mean(axis=0)
        rows.append({
            "id": f.id, "name": f.name, "kind": f.kind, "material": f.material, "building": f.building,
            "nx": f.normal[0], "ny": f.normal[1], "nz": f.normal[2], "area": round(area, 6),
            "cx": round(float(centroid[0]), 6), "cy": round(float(centroid[1]), 6), "cz": round(float(centroid[2]), 6),
            "quads": len(f.quads),
        })
    return rows


def write_facets(city: City, path: Path) -> None:
    rows = facet_rows(city)
    with path.open("w", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=list(rows[0]))
        w.writeheader()
        w.writerows(rows)


def write_materials(city: City, spk: Path, path: Path, temperatures: dict[int, float] | None = None,
                    default_t: float = 300.0) -> None:
    """htrdr-materials file: one line per facet, plus the air."""
    mat_dir = spk.resolve() / "materials"
    lines = ["air none"]
    for f in city.facets:
        t = (temperatures or {}).get(f.id, default_t)
        lines.append(f"{f.name} {mat_dir / (f.material + '.mrumtl')} {t:.3f}")
    path.write_text("\n".join(lines) + "\n")


def summary(city: City) -> dict:
    kinds: dict[str, int] = {}
    for f in city.facets:
        kinds[f.kind] = kinds.get(f.kind, 0) + 1
    c = city.config
    return {
        "config": asdict(c),
        "tile_size_m": c.tile_size,
        "buildings": len(city.buildings),
        "towers": sum(b.tower for b in city.buildings),
        "max_height_m": max((b.floors for b in city.buildings), default=0) * c.floor_height,
        "facets": len(city.facets),
        "facets_by_kind": kinds,
        "triangles": 2 * sum(len(f.quads) for f in city.facets),
        "materials_used": sorted({f.material for f in city.facets}),
    }


DEFAULT_SPK = Path.home() / "htrdr-data/spk/htrdr-Atmosphere-Starter-Pack-0.8.0"


def main(argv: list[str] | None = None) -> None:
    ap = argparse.ArgumentParser(description="Generate a procedural city tile for htrdr-atmosphere.")
    ap.add_argument("out", type=Path, help="output directory")
    ap.add_argument("--seed", type=int, default=1)
    ap.add_argument("--blocks", type=int, default=3)
    ap.add_argument("--spk", type=Path, default=DEFAULT_SPK, help="starter pack directory (for material paths)")
    args = ap.parse_args(argv)

    city = generate(CityConfig(seed=args.seed, blocks=args.blocks))
    args.out.mkdir(parents=True, exist_ok=True)
    write_obj(city, args.out / "city.obj")
    write_facets(city, args.out / "facets.csv")
    write_materials(city, args.spk, args.out / "city.mtls")
    s = summary(city)
    (args.out / "city.json").write_text(json.dumps(s, indent=2) + "\n")
    print(json.dumps({k: v for k, v in s.items() if k != "config"}, indent=2))


if __name__ == "__main__":
    main()
