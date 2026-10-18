import os
import shutil
import subprocess
from collections import Counter
from pathlib import Path

import numpy as np
import pytest

from citylapse.city import (DEFAULT_SPK, CityConfig, generate, ground_kind, write_facets,
                            write_materials, write_obj)


@pytest.fixture(scope="module")
def city():
    return generate(CityConfig(seed=1))


def parse_obj(path: Path):
    """Return (vertices, [(group, (i, j, k))]) with 0-based indices."""
    verts, tris, group = [], [], None
    for line in path.read_text().splitlines():
        if line.startswith("v "):
            verts.append([float(x) for x in line.split()[1:]])
        elif line.startswith("usemtl "):
            group = line.split()[1]
        elif line.startswith("f "):
            assert group is not None, "triangle outside any usemtl group"
            idx = [int(x) - 1 for x in line.split()[1:]]
            assert len(idx) == 3, "htrdr-obj is triangles only"
            tris.append((group, tuple(idx)))
    return np.array(verts), tris


@pytest.fixture(scope="module")
def obj(city, tmp_path_factory):
    path = tmp_path_factory.mktemp("city") / "city.obj"
    write_obj(city, path)
    return parse_obj(path)


def test_same_seed_same_files_and_different_seed_differs(tmp_path):
    for name, seed in [("a", 7), ("b", 7), ("c", 8)]:
        write_obj(generate(CityConfig(seed=seed)), tmp_path / f"{name}.obj")
    a, b, c = ((tmp_path / f"{n}.obj").read_bytes() for n in "abc")
    assert a == b
    assert a != c


def test_every_group_is_a_facet_against_air(city, obj):
    _, tris = obj
    groups = {g for g, _ in tris}
    assert all(g.endswith(":air") and g.count(":") == 1 for g in groups)
    names = [f.name for f in city.facets]
    assert len(set(names)) == len(names)
    assert groups == {n + ":air" for n in names}
    assert len(tris) == 2 * sum(len(f.quads) for f in city.facets)


def test_triangle_normals_point_into_the_air(city, obj):
    verts, tris = obj
    normal = {f.name + ":air": np.array(f.normal, dtype=float) for f in city.facets}
    for g, (i, j, k) in tris:
        n = np.cross(verts[j] - verts[i], verts[k] - verts[i])
        length = np.linalg.norm(n)
        assert length > 1e-9, "degenerate triangle"
        assert np.allclose(n / length, normal[g]), g


def test_surface_is_closed_except_the_tile_border(city, obj):
    verts, tris = obj
    size = city.config.tile_size
    directed = Counter()
    for _, (i, j, k) in tris:
        for a, b in ((i, j), (j, k), (k, i)):
            directed[(a, b)] += 1
    assert max(directed.values()) == 1, "an edge is used twice in the same direction"

    def on_border(a, b):
        pa, pb = verts[a], verts[b]
        if pa[2] != 0 or pb[2] != 0:
            return False
        return any(pa[ax] == pb[ax] and pa[ax] in (0.0, size) for ax in (0, 1))

    open_edges = [(a, b) for (a, b) in directed if (b, a) not in directed]
    assert open_edges, "the ground must have a border"
    assert all(on_border(a, b) for a, b in open_edges)
    # The border is the whole tile perimeter, one edge per cell.
    assert len(open_edges) == 4 * city.config.tile_cells


def test_geometry_fills_exactly_one_tile(city, obj):
    verts, _ = obj
    size = city.config.tile_size
    assert verts[:, :2].min() == 0 and verts[:, :2].max() == size
    assert verts[:, 2].min() == 0


def test_tile_edges_are_streets_so_repetition_is_seamless(city):
    c = city.config
    n = c.tile_cells
    for t in range(n):
        for i, j in [(0, t), (n - 1, t), (t, 0), (t, n - 1)]:
            assert ground_kind(c, i, j) == "street"


def test_buildings_stay_off_streets_and_never_touch(city):
    c = city.config
    n = c.tile_cells
    owner = np.full((n, n), -1)
    for b in city.buildings:
        assert b.x1 - b.x0 >= 4 and b.y1 - b.y0 >= 4
        assert (owner[b.x0:b.x1, b.y0:b.y1] == -1).all(), "overlapping footprints"
        owner[b.x0:b.x1, b.y0:b.y1] = b.id
        for i in range(b.x0, b.x1):
            for j in range(b.y0, b.y1):
                assert ground_kind(c, i, j) == "interior"
    # A one-cell ring around each building holds no other building.
    for b in city.buildings:
        ring = owner[max(b.x0 - 1, 0):b.x1 + 1, max(b.y0 - 1, 0):b.y1 + 1]
        assert set(np.unique(ring)) <= {-1, b.id}


def test_park_and_towers(city):
    kinds = Counter(f.kind for f in city.facets)
    assert kinds["park"] > 0
    assert sum(b.tower for b in city.buildings) == city.config.towers
    tallest = max(b.floors for b in city.buildings)
    assert tallest >= city.config.tower_floors[0]


def test_facet_table_and_materials_file(city, tmp_path):
    write_facets(city, tmp_path / "facets.csv")
    rows = (tmp_path / "facets.csv").read_text().splitlines()
    assert len(rows) == len(city.facets) + 1
    # Ground and roofs face up, walls are vertical; areas add up per kind.
    c = city.config
    ground = sum(len(f.quads) for f in city.facets if f.kind in ("street", "sidewalk", "yard", "park"))
    footprints = sum((b.x1 - b.x0) * (b.y1 - b.y0) for b in city.buildings)
    assert ground + footprints == c.tile_cells ** 2

    write_materials(city, Path("/spk"), tmp_path / "city.mtls", temperatures={0: 312.5})
    lines = (tmp_path / "city.mtls").read_text().splitlines()
    assert lines[0] == "air none"
    assert len(lines) == len(city.facets) + 1
    first = lines[1].split()
    assert first[0] == city.facets[0].name
    assert first[1] == f"/spk/materials/{city.facets[0].material}.mrumtl"
    assert first[2] == "312.500"
    assert lines[2].split()[2] == "300.000"


needs_htrdr = pytest.mark.skipif(
    shutil.which("htrdr-atmosphere") is None and not (Path.home() / ".local/bin/htrdr-atmosphere").exists(),
    reason="htrdr-atmosphere not installed")
needs_spk = pytest.mark.skipif(not DEFAULT_SPK.is_dir(), reason="starter pack not fetched")


@needs_spk
def test_every_material_file_exists(city):
    for m in {f.material for f in city.facets}:
        assert (DEFAULT_SPK / "materials" / f"{m}.mrumtl").is_file(), m


@pytest.mark.htrdr
@needs_htrdr
@needs_spk
def test_htrdr_renders_the_city(city, tmp_path):
    write_obj(city, tmp_path / "city.obj")
    write_materials(city, DEFAULT_SPK, tmp_path / "city.mtls")
    env = dict(os.environ, LD_LIBRARY_PATH=str(Path.home() / ".local/lib"))
    exe = shutil.which("htrdr-atmosphere") or str(Path.home() / ".local/bin/htrdr-atmosphere")
    out = tmp_path / "img.txt"
    size = city.config.tile_size
    cmd = [exe, "-a", str(Path.home() / "htrdr-data/etc/ecrad_opt_prop.txt"),
           "-R", "-g", str(tmp_path / "city.obj"), "-M", str(tmp_path / "city.mtls"),
           "-D", "120,50", "-P", f"pos={size / 2},{size / 2},500:tgt={size / 2},{size / 2},0:up=0,1,0:height={size}",
           "-i", "def=48x48:spp=4", "-t", "8", "-f", "-o", str(out)]
    # SIGFPE on this machine is a hardware fault (see PLAN.md); retry it.
    for _ in range(3):
        r = subprocess.run(cmd, env=env, capture_output=True, text=True)
        if r.returncode != -8 and r.returncode != 136:
            break
    assert r.returncode == 0, r.stderr[-2000:]
    rows = [l.split() for l in out.read_text().splitlines() if l.strip() and not l.startswith("#")]
    assert rows[0][:2] == ["48", "48"]
    y = np.array([float(r[2]) for r in rows[1:]])
    # At 4 spp, shadowed pixels lit only by skylight are often exactly 0 (no
    # path scattered towards the sun), so check the image as a whole.
    assert np.isfinite(y).all() and (y >= 0).all()
    assert y.mean() > 1 and (y > 0).mean() > 0.5
