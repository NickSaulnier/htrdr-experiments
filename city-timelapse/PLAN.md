# City time-lapse and 3D data views — plan

A small procedural city rendered with htrdr-atmosphere over one day:

- a side-by-side time-lapse of visible (CIE XYZ) and 9–10 µm thermal frames as the sun moves (`-D`);
- street-level flux maps at each time step, showing shading and heat load;
- a ParaView figure that puts the `-d` cloud octrees, the cloud field, the city and the flux planes in one 3D view.

Stretch: an MPI scaling study (strong scaling on one frame, throughput across many frames).

## What htrdr 0.12 gives us, and what it doesn't

These facts from the man pages shape the design:

- **Ground** (`-g`) is an htrdr-obj file: triangles only, each in a `usemtl front[:interface]:back` group. A face is front-facing when its vertices are clockwise. `-R` repeats the ground infinitely along X and Y.
- **Materials** (`-M`): one line per name, `name /abs/path.mrumtl temperature`, or `name none` for the sky (`-n`, default `air`).
- **The surface temperature is an input.** It is fixed per material name, and htrdr does not solve an energy balance. A thermal time-lapse where sunlit walls warm up needs us to supply temperatures for every time step. M3 does this with a simple prescribed model and labels it as such. It is not a coupled simulation.
- **Flux maps** (`-p pos:tgt:up:sz`) store W/m² in components 1–2 (mean, σ). Only the part of the sensor outside any geometry gets a flux, so building footprints are masked. M0 checks what masked pixels contain.
- **`-d`** writes the cloud acceleration octrees as legacy VTK, separated by `---` lines, one octree per spectral band. Each node holds the min and max extinction. This needs clouds (`-c`, `-m`).
- **Sun direction:** `-D az,el` with the azimuth counter-clockwise from +X. With +X east and +Y north, htrdr az = 90° − compass azimuth. Elevation must lie in [0, 90], so night frames are thermal only.
- **Longwave:** use the band `lw=9000,10000`. Monochromatic `lw` brightness temperature is off by 10⁹ in 0.12 (see render-agent/README.md).
- **`-O` cache** was not written for clear-sky scenes. With clouds it should be, and M0 checks this.
- **Hardware:** this machine's i9-13900K (microcode 0x10F) causes intermittent SIGFPE under load (branch `claude/htrdr-sigfpe-root-cause`). The frame runner retries a crashed render once with half the threads, as the MCP server does. The real fix is a BIOS update.

## Inputs

- **Gas:** `~/htrdr-data/etc/ecrad_opt_prop.txt` (already present).
- **htrdr Atmosphere Starter Pack 0.8.0** from meso-star.com (1.8 GB, SHA-512 published):
  - SLUM urban materials as mrumtl files (`materials/slum`, from Kotthaus et al. 2013: SW reflectance 0.3–2.5 µm and LW emissivity 8–14 µm for 74 impervious urban samples);
  - cloud fields (`DZVAR.1.ARMCU.008.diaKCL.htcp`, continental cumulus with its base near 1 km);
  - Mie optics (`Mie_LUT_Cloud-2-10-0.010.nc`).
  Its 19.5M-triangle `city.obj` is a reference only; we generate our own, smaller city.
- **Site and date (defaults):** 43.6° N, 1.44° E (Toulouse, where meso-star is based), 21 June, local solar time. These are configurable.

## Layout

`city-timelapse/` is a uv Python package (`citylapse`). It depends on `../render-agent` by path, reusing `argv`, `runner`, `image`, `stats` and `estimate` instead of copying them. Everything runs in WSL.

```
city-timelapse/
  PLAN.md  README.md  pyproject.toml
  scripts/fetch-starter-pack.sh
  src/citylapse/
    city.py        procedural city → htrdr-obj + facet table
    materials.py   htrdr-materials writer (per-step temperatures)
    sun.py         NOAA solar position → htrdr -D
    thermal.py     prescribed surface temperature model
    frames.py      day schedule, per-frame jobs, resumable manifest
    compose.py     tone mapping, side-by-side frames, clock, colorbar
    flux.py        street-level flux maps, daily integrals
    vtk.py         flux planes, htcp clouds, octree split → VTK
    paraview/figure.py   pvpython scene script
    scaling.py     MPI study driver (stretch)
  tests/
  out/            (git-ignored) renders, frames, videos
```

## Milestones

### M0: Data, tools and spikes

- `scripts/fetch-starter-pack.sh`: download the pack, check its SHA-512, and unpack it to `~/htrdr-data/spk/`.
- Spikes, each with timings and findings recorded in this file:
  1. Render the pack's `plane.obj` with one SLUM material in visible and in `lw=9000,10000`, to confirm the materials and paths work.
  2. **Many materials:** check that a materials file with about 5,000 names pointing at a few mrumtl files loads and renders. The per-facet temperatures in M3 depend on this. If it fails, fall back to temperature bins of 1 K.
  3. **Flux sensor:** a 1 m-high `-p` sensor over a plane with a box on it. Record what the masked pixels contain and how long a 256² map takes.
  4. **`-d` dump:** dump the DZVAR octrees, count them, split them with csplit, and check that one opens as VTK.
  5. **Cache:** check that `-O` is written and reused with clouds, and how much preprocessing time it saves.
- Done when every spike has a measured result and a cost estimate for one frame at the target resolution.

#### M0 findings

All runs below are reproducible with `spikes/m0.sh` (box scene: a 200 m grey ground, ρ = 0.3, with a 20 × 20 × 30 m box at the centre; `-D 135,40`; 32 threads).

- **usemtl sides.** A ground quad wound clockwise seen from above, written `usemtl white:black`, shows the *black* BRDF to a camera above it: mean Y 0.65 against 29.8 for `black:white`, a ratio of 46 for reflectivities 0.9 and 0.02. So, seen from the air side, the second name is what a ray hits. An interface name in the middle (`white:air:black`) changes nothing. With `mat:air` or `air:mat` the output is identical either way, in visible and in longwave: htrdr takes the BRDF from the side that isn't the sky material. The pack's own `city.obj` writes `usemtl <mat>:air` (and `<mat>:internal_air`, `glass:air`). The generator follows that, with outward faces wound consistently and tested, so nothing depends on this leniency beyond what meso-star's own scene relies on.
- **Many materials work.** One material name per ground tile, all pointing at the same mrumtl file, with temperatures 290–329 K, longwave at 100² and 64 spp:

  | names | wall time | max RSS |
  |---|---|---|
  | 1 | 0.48 s | 105 MB |
  | 100 | 0.47 s | 105 MB |
  | 5,000 | 0.51 s | 115 MB |
  | 20,000 | 0.61 s | 141 MB |

  The brightness temperature follows the tiles (range 271–321 K with 100 tiles). Per-facet temperatures (M3) are fine as planned, with no binning needed.
- **Flux sensor masking is shortwave only.** With `sw=300,4000`, pixels inside the box footprint are exactly 0 (the 99 zero pixels are the 10 × 10 footprint at 2 m per pixel). With `lw=4000,100000` they are *not* masked: they read 444 W/m², the flux from the box's 300 K inner walls. The flux code (M5) masks footprints itself, from the city geometry, for every band.
- **Flux map orientation.** For `-p pos=0,0,1:tgt=0,0,2:up=0,1,0` (looking up, up = +Y), row 0 is +Y (north) and **columns increase toward −X**, since the sensor looks at the sky from below. The box shadow (sun towards −X, +Y) has its centroid at column 41, row 58, against 49.5, 49.5 for the footprint. The map is therefore mirrored relative to a map view, and M5 flips the columns before anything uses it.
- **Cost on the box scene,** 960 × 540 at 64 spp, infinite ground (`-R`):

  | band | wall time | CPU time | memory | noise at 64 spp |
  |---|---|---|---|---|
  | visible | 2.2 s | 33 s | 171 MB | Y relative error: median 9.1 %, p95 31 % |
  | thermal (`lw=9000,10000`) | 2.8 s | 65 s | 234 MB | Tb σ mean 4.7 K |

  Thermal paths are faster (1.7 µs against 3.1 µs) but there's one set per pixel against three for XYZ. For σ(Tb) ≈ 0.5 K, about 90× the spp are needed, roughly 4 min per thermal frame. Visible at a 1 % median needs about 80×, roughly 3 min. The city will cost more per path than this box, so these are lower bounds, to be remeasured after M1.
- **SIGFPE.** Two of 8 full-load 960 × 540 visible runs died with SIGFPE (exit 136). A crashed run leaves a **0-byte output**, because `-f` truncates the file first. The frame runner must:
  - write to a temporary name and rename it only on exit 0;
  - retry, as the MCP server does.
- **Not bit-reproducible.** Repeated identical runs give different output, with file sizes varying by a few kB. The noise isn't frozen between frames, so frame-to-frame flicker is set only by σ.
- **Starter pack.** Fetched and SHA-512 verified (38 min at about 0.7 MB/s). It unpacks to 4.2 GB in `~/htrdr-data/spk/htrdr-Atmosphere-Starter-Pack-0.8.0`, and the 1.8 GB archive is kept next to it. The pack's materials files use `"${HTRDR_ATMOSPHERE_SPK}/…"` paths. Ours write absolute paths.
- **SLUM materials** (`spikes/slum_table.py`): 74 lambertian files, each with 1,040 wavelengths over 0.35–14 µm, but **nothing between 1.0 and 8.0 µm**. htrdr interpolates across that gap, which matters for the broadband `sw=300,4000` flux and `lw=4000,100000` flux, but not for visible or 9–10 µm. The M3 model uses its own broadband albedo, taken from the 0.35–1.0 µm part, and says so.
  - Visible albedo ranges from 0.05 (L002, Z008) to 0.58 (S004).
  - 9–10 µm emissivity is 0.81–0.97 for everything except Z002, at 0.19, which is metallic.
  - The letter prefixes (A, B, C, G, L, R, S, V, X, Z) are SLUM categories. M1 checks their names against the SLUM documentation before assigning roofs, walls and streets.
- **Cloud field** (DZVAR ARMCU, `spikes/htcp_info.py`):
  - 256 × 256 × 160 cells over 6.4 × 6.4 km (25 m), z irregular up to 4.2 km, one time step;
  - liquid water in 1.7 % of cells (about 25 % cloud cover), cloud base near 1 km.
- **`-d` dumps the octrees for the `-s` spectral domain only.**
  - The default (visible) gives **30 octrees**: one per k-distribution quadrature point, in 4 bands from 345 to 1242 nm (6, 6, 8 and 10 points). That's 750 MB of ASCII VTK; it took 16.5 s and 0.97 GB of memory.
  - `-s lw=9000,10000` gives 20 octrees in 2 bands (8,475–9,259 nm and 9,259–10,204 nm), 559 MB.
  - Each octree is an `UNSTRUCTURED_GRID` of about 250k–270k cells, with a `Kext` cell array of 2 components (min, max). The man page's `csplit` recipe splits them correctly.
  - For M6, one octree per band is enough, and the bands are near-duplicates, so the figure shows one visible and one 9–10 µm octree.
- **`-O` cache works with clouds.**
  - It holds 478 MB for DZVAR. A 160 × 90 × 16 spp cloudy frame takes 8.9 s when it builds the cache and 1.4 s when it reuses it.
  - A run that crashed after preprocessing still left a usable cache.
  - The cache depends on the input files, not on `-D` or the camera, so one cache serves a whole day of frames.
- **SIGFPE is much more frequent with clouds:** 4 of 6 cloudy runs crashed, against 2 of 8 on the box scene.
  - It always fails in glibc `sincos`, called from `ssp_ran_sphere_cap_uniform_local` (sampling the sun's cone), the same signature as the hardware fault on `claude/htrdr-sigfpe-root-cause`.
  - At this rate, a day of cloudy frames isn't practical without the BIOS microcode update. Retries alone would waste a lot of work on long frames, since a crash late in a frame throws away the whole frame.
  - Until the update, the runner retries up to 3 times, and M4 runs clear-sky frames (`-c` off) first, adding clouds once the machine is fixed.

### M1: Procedural city

- A deterministic generator (seeded) for a cyclic tile, so `-R` repeats it without seams:
  - a grid of blocks with N–S and E–W streets on the tile edges;
  - buildings as boxes with varied heights and footprints, and a few towers;
  - asphalt streets, concrete sidewalks, roofs (several SLUM roofing types), brick, concrete and glass-like walls, and a small park.
- Writes `city.obj` in htrdr-obj form with the correct clockwise winding, so `air` is on the outside, plus a facet table (id, kind, orientation, area, centroid, material).
- Each facet group gets its own material name (for example `wall_brick_b012_s`), so M3 can set its temperature.
- Tests:
  - every triangle is in a group;
  - every normal points into the air;
  - buildings are closed;
  - the tile edges match;
  - the same seed gives the same file.
- Optional: an import script for the Blender 5.1 install on Windows, for an interactive look.
- Done when a visible render at noon looks right (in the viewer) and the tests pass.

#### M1 results

`uv run citylapse-city out/city-s1 --seed 1` writes `city.obj`, `facets.csv`, `city.mtls` (300 K everywhere until M3) and `city.json` (a summary) in 3.5 s. `src/citylapse/city.py`, 11 tests in `tests/test_city.py` (one marked `htrdr` renders the city for real).

- **The tile.** 240 m square, 3 × 3 blocks on an 80 m pitch:
  - 12 m streets centred on the block lines, so the tile edges are mid-street;
  - 4 m sidewalks;
  - 60 m block interiors split into 2–3 × 2–3 lots;
  - one park block.
- **Buildings.** 85 % of lots get a building, with lognormal heights (median 6 floors of 3 m, 2–15 floors). The two largest footprints become towers of 18–28 floors.
  - Street-facing walls sit on the sidewalk 70 % of the time.
  - Interior sides step back 2 m, so neighbours are at least 4 m apart and never share a wall.
  - Seed 1 gives 43 buildings up to 57 m.
- **One integer lattice.** All geometry sits on a lattice of 2 m horizontally by 3 m vertically. Ground cells, roof cells and wall columns share edges exactly, so the mesh is conforming. The tests check that:
  - every directed edge has exactly one opposite, except along the tile border, which is exactly 4 × 120 edges;
  - every triangle's right-hand normal equals its facet's outward normal;
  - ground cells plus footprints exactly tile the square.
- **Facets** (material names, and so temperatures): ground 2 × 2 m, roofs 4 × 4 m, walls 4 m wide × one floor. Seed 1 has 18,197 facets (56,832 triangles): street 3,996, sidewalk 2,304, yard 2,340, park 900, roof 1,331, wall 7,326.
- **usemtl convention**, matching the pack's `city.obj` and `plane.obj`: the two names are the media on the two sides, the front being the side from which the vertices look clockwise. A ray coming from one side hits the other side's material. The generator winds every quad counter-clockwise seen from the air and writes `usemtl <facet>:air`, so the front medium is the solid. This is the M0 `white:black` result read the right way round.
- **Materials.** SLUM's paper and documentation (which name the categories) couldn't be fetched (the ScienceDirect and Reading PDFs refuse automated access), so the roles follow meso-star's own `city.obj`:
  - **Walls:** B, C and S codes (B001, B002, B005, B007, B008, B011, B013, C003, C006, C008, S004, S005), one per building.
  - **Roofs:** R, G and Z codes plus A008 (R001, R002, R004, R009, R012, G001, G004, Z001, A008).
  - **Streets:** A003.
  - **Sidewalks:** C004.
  - **Yards:** S002, G002 or C001.
  - **Park:** `legacy/grass`.

  Glass and pitched roofs are left out for now: all walls are opaque lambertian and all roofs are flat.
- **Renders** (`out/city-s1/top.png` and `oblique.png`, sun at `-D 120,50`):
  - the tile repeats seamlessly;
  - shadows fall away from the sun;
  - neither the orthographic top view (`up=0,1,0`) nor the perspective view is mirrored (checked against the tower positions).
- **Cost per city frame**, 960 × 540 at 64 spp:

  | band | wall time | CPU time | memory | noise |
  |---|---|---|---|---|
  | visible | 14.4 s | 332 s | 0.85 GB | Y relative error: median 11.6 %, p95 60 % |
  | thermal | 6.1 s | 69 s | 0.84 GB | Tb σ mean 1.9 K, median relative 0.63 % |

  - **Load time:** about 3.7 s and 640 MB of each run is htrdr parsing the 18,197 material lines, each of which re-reads its 1,040-wavelength SLUM file (the box scene loads in 0.4 s). That's acceptable per frame. If it ever matters, resampled mrumtl copies would cut it.
  - **Visible is the expensive band:** shadowed street canyons are lit only by skylight, and at low spp many of their pixels are exactly 0. A 1 % median would need about 135 × 64 spp, roughly 30 min per 960 × 540 frame. **M4 therefore targets 3 % median relative error at 640 × 360.** That's (11.6/3)² ≈ 15× the spp on 0.44× the pixels, about 75 s per frame once the 10.7 s of 64-spp rendering time is scaled.
  - **Thermal:** σ(Tb) ≈ 0.5 K needs about 14×, so about 40 s per frame at 960 × 540 or 20 s at 640 × 360.
  - A day at 15-minute steps (about 60 visible frames and 96 thermal frames) is therefore roughly 1.8 hours, before crash retries.
- **SIGFPE:** 3 of the 4 attempts at the visible city frame crashed, so the chunked rendering planned in M2 is needed, not optional.
- Not done: the optional Blender import script.

### M2: Sun path and frame runner

- `sun.py`: the NOAA solar position algorithm (no dependencies), tested against published values, plus the conversion to `-D`.
- `frames.py`: builds a day schedule (default every 15 min). Each step has a visible job (sun above about 2°), a thermal job and the flux jobs.
- Jobs are idempotent: each output carries a manifest (exact argv, input hashes, timings, retries), and a rerun skips finished frames.
- Long frames are rendered as several independent chunks of spp/k each, then merged:
  - pixel means are averaged with spp weights;
  - variances combine as Σ nᵢ² σᵢ² / n².
  A SIGFPE then loses one chunk instead of a whole frame (M0 measured 4 of 6 cloudy runs crashing). Tests check that merged chunks agree with a single run within σ.
- spp comes from render-agent's estimator, with a per-frame target error, so noise is the same across frames and the time-lapse doesn't flicker more at dusk.
- Done when a 4-frame coarse day runs end to end and resumes after a kill.

### M3: Surface temperature model

htrdr only reads temperatures, so this model supplies them. It is deliberately simple and documented as prescribed:

- **Direct sun per facet:** a shadow test from sample points on each facet to the sun (numpy ray–box tests are enough for a box city), giving a sunlit fraction and the cosine of incidence.
- **Diffuse sky:** a precomputed sky view factor per facet.
- **Temperature:** a first-order ODE per facet, `C dT/dt = (1−ρ) S_abs − h (T − T_air(t)) − ε σ (T⁴ − T_sky⁴)`, using:
  - the facet's SLUM reflectance and emissivity;
  - a diurnal air temperature curve;
  - thermal inertia per surface kind (roof, wall, street).
- Writes `materials_<step>.mtls` for each time step.
- Tests:
  - shaded facets are cooler than sunlit ones;
  - roofs peak after solar noon;
  - east walls peak in the morning and west walls in the afternoon;
  - the energy balance holds.

### M4: Time-lapse

- Renders all visible and thermal frames from one oblique perspective camera (about 300 m away, looking down at about 30°).
- **Fixed tone mapping across the sequence:** auto-white would flicker. The visible frames use htpp image mode with one exposure and white taken from the noon frame. The thermal frames use map mode with a fixed Tb range (for example 285–335 K), an inferno palette and a colorbar.
- `compose.py` puts visible on the left and thermal on the right, with a clock and the sun elevation. Night frames show a dark left panel.
- ffmpeg makes `day.mp4` (H.264, yuv420p, 24 fps), plus a frame strip PNG.
- Run logs link frames to the browser viewer, as in render-agent M5.
- Done when the full-day video is rendered and its noise is within the target.

### M5: Street-level flux maps

- For each time step:
  - an upward-facing `-p` sensor at 1 m covering the tile, with `sw=300,4000` (downwelling solar) and `lw=4000,100000` (downwelling thermal from the sky and walls);
  - the total incoming flux (heat load) as their sum, with σ added in quadrature.
- Daily integrals in kWh/m² and hours of direct sun per pixel.
- A flux time-lapse panel that can be added to the M4 video as a third panel.
- Done when the maps match physical checks: open-street noon SW is close to a plane-only render, and street canyons show their shadows in the expected places.

### M6: ParaView 3D figure

- `vtk.py`:
  - splits the `-d` dump into one `.vtk` file per octree and picks the band used in the visible frames;
  - converts the htcp liquid water content to `.vti`;
  - writes the flux maps as `.vti` planes at z = 1 m, with mean and σ arrays.
- `paraview/figure.py` (pvpython) builds the scene:
  - the city surface coloured by temperature;
  - the flux plane;
  - the LWC isosurface;
  - octree leaves as wireframes coloured by max extinction, with a clip to show the partition.
- Saves a PNG at fixed camera settings and a `.pvsm` state file.
- Clouds are at about 1 km and the city tile is a few hundred metres, so the figure uses `-r` and frames a region that shows both. If needed, a second inset view shows the octree in the cloud domain.

### M7 (stretch): MPI scaling

- **Strong scaling:** one frame with a fixed spp, run with 1 to N ranks × threads.
  - Locally: 1, 2, 4 ranks × (32/ranks) threads with OpenMPI 4.1.2.
  - On a cluster: 1 to many nodes, if one is available.
- **Throughput:** a batch of frames run as independent single-node jobs versus one MPI job per frame. Measured in frames per hour.
- Results: speedup and efficiency plots, with input load time separated from rendering (the cache helps here).

## Open questions

- Is there a cluster for M7? Its scheduler (Slurm?), node count, and whether htrdr can be built there with the same pinned versions.
- Video resolution and frame interval (defaults are 960×540 per panel and 15 min) depend on the M0 cost per frame.
