# htrdr MCP server

An [MCP](https://modelcontextprotocol.io) server that wraps [htrdr](https://www.meso-star.com/projects/htrdr/htrdr.html), the Monte Carlo radiative transfer renderer. An agent can describe a scene, render it, and keep refining it until a target uncertainty is met. Built with [FastMCP](https://gofastmcp.com).

The agent never handles pixels. It gets summaries of each render's per-pixel error and cost, plus a proposal for the next render: how many samples per pixel (spp) the target needs, and how long that will take. Every render's exact command line is recorded.

This is part of the render-agent work planned in [`PLAN.md`](PLAN.md). The LangGraph agent that uses these tools comes later.

## How it's used

1. `build_scene` with a full scene spec. It returns a `scene_id` and warnings.
2. `render` the scene at a low spp, as a preview.
3. `read_stats` with a `target_rel_err`. It returns the error percentiles and a `proposal`.
4. `render` again at `proposal.spp`, and repeat until `proposal.target_met` is true.
5. `postprocess` for a PNG. The record at `htrdr://renders/{render_id}` keeps the exact command line.

The real example from development: a 10 µm view from 2 km with a target of p95 ≤ 1 %, rendered at 160×120.

| spp | wall time | p95 relative error |
|---|---|---|
| 8 | 0.58 s | 61.3 % |
| 128 | 0.63 s | 11.6 % |
| 2,048 | 1.81 s | 2.79 % |
| 19,127 | 11.0 s | **0.92 %** (target met) |

## Setup

Everything runs in Linux; on Windows that means WSL (developed on Ubuntu 22.04).

**1. Build htrdr into `~/.local`.** First install the system packages (this is the only step that needs `sudo`):

```bash
sudo apt-get install -y build-essential pkg-config git libopenmpi-dev openmpi-bin libnetcdf-dev netcdf-bin librandom123-dev libtbb-dev cmake mandoc
```

Then build. This takes a few minutes, most of it compiling Embree:

```bash
bash render-agent/scripts/build-htrdr.sh
```

**2. Fetch the gas optical properties** that ship with htgop. That's enough for clear-sky scenes. The script checks the download's SHA-256.

```bash
bash render-agent/scripts/fetch-data.sh
```

The file lands at `~/htrdr-data/etc/ecrad_opt_prop.txt`.

**3. Install the Python project** with [uv](https://docs.astral.sh/uv/) (Python 3.12):

```bash
cd render-agent && uv sync
```

## Running the server

The server speaks MCP over stdio:

```bash
uv run htrdr-mcp --runs-dir ../runs
```

| Option | Meaning |
|---|---|
| `--runs-dir DIR` | Where scenes, renders and caches are kept (default `$HTRDR_RUNS`, else `./runs`) |
| `--prefix DIR` | htrdr's install prefix (default `$HTRDR_PREFIX`, else `~/.local`) |
| `--threads N` | Passed to htrdr as `-t` (default: all cores) |
| `--fake` | Use the fake htrdr instead: same command line, synthetic images, no real renders |

htrdr's shared libraries are found from the prefix automatically, and htpp is used for `postprocess` when it's installed in the same prefix. FastMCP prints a startup banner on stderr; stdout carries only the protocol.

To register it with an MCP client, point the client at that command. For a client running inside WSL:

```json
{
  "mcpServers": {
    "htrdr": {
      "command": "/path/to/render-agent/.venv/bin/htrdr-mcp",
      "args": ["--runs-dir", "/path/to/runs"]
    }
  }
}
```

A client running on Windows would have to start it through `wsl`; that setup hasn't been tested yet.

## Tools

### `build_scene(spec)`

Registers a scene. The same spec always gives the same `scene_id`. The server checks that every input file exists where htrdr will look.

```json
{
  "inputs":   { "atmosphere": "/home/me/htrdr-data/etc/ecrad_opt_prop.txt" },
  "spectral": { "mode": "lw", "range_nm": [10000, 10000] },
  "sun":      { "azimuth_deg": 180, "elevation_deg": 40 },
  "sensor":   { "kind": "perspective", "pos": [0, 0, 2000], "tgt": [1000, 0, 2000], "fov_deg": 60 },
  "image":    { "width": 160, "height": 120 }
}
```

| Field | Meaning |
|---|---|
| `inputs` | `atmosphere` (htgop, required). Optional: `clouds` (htcp, which needs `mie`), `ground` and `materials`, `repeat_clouds`, `repeat_ground`, `sky_material`. |
| `spectral.mode` | `cie_xyz` (visible, the default), `lw` (thermal emission) or `sw` (solar). `lw` and `sw` need `range_nm`; equal bounds mean monochromatic. Optional `tref_k`. |
| `sun` | `azimuth_deg` in [0, 360) and `elevation_deg` in [0, 90] |
| `sensor` | `perspective` (`fov_deg`, vertical), `orthographic` (`height_m`) or `flux` (`size_m`). All take `pos`, `tgt` and `up`, in metres; altitude is the z of `pos`. |
| `image` | `width` and `height` in pixels (default 320×240) |

**Returns:** `scene_id`, `warnings`, `cache_key`, and `command_template`, the htrdr command line every render will run, with `<spp>` and `<output>` to fill in.

**Read the warnings.** They flag specs htrdr accepts that probably aren't what was meant:
- A sun in `lw` mode has no effect, because htrdr computes thermal emission only.
- `sw` or `cie_xyz` without a sun uses htrdr's default sun.
- A downward view without a ground only sees the atmosphere below the horizon.
- Monochromatic `lw` triggers the brightness-temperature issue described below.

### `render(scene_id, spp, timeout_s=None)`

Runs htrdr once and waits for it. Progress is reported as MCP progress notifications, from htrdr's own percentage. Renders run one at a time, since htrdr already uses every core.

**Returns:**
- `render_id`, `status` (`ok`, `failed`, `timeout` or `cancelled`), `wall_s`
- `predicted_wall_s`, from earlier renders of the scene
- `command_line`, `output` and a `message`
- `stderr_tail`, htrdr's last output, when the render didn't succeed

A failed or timed-out render is returned with that status rather than raised as an error. A bad `scene_id` or `spp` is a tool error. If the client cancels the call, htrdr is stopped. The default timeout is one hour.

### `read_stats(render_id, target_rel_err=None, percentile=95, max_spp=None, budget_s=None)`

Error and cost summaries of a finished render, never its pixels.

| Key | Contents |
|---|---|
| `quantities` | Mean, min and max of each quantity: X, Y, Z for CIE XYZ; brightness temperature and radiance for `lw`; radiance for `sw` |
| `error` | Per-pixel relative error σ/\|μ\| of **Y** (CIE XYZ) or the **radiance** (`lw`, `sw`): `percentiles` (p50, p90, p95, p99), `max`, `fraction_meeting_target`, and the 5 `worst_tiles` on an 8×8 grid |
| `cost` | `mean_time_per_path_us`, `paths`, and `cpu_s`, the CPU time spent |
| `cost_model` | Wall time ≈ `fixed_s` + `per_spp_s` × spp, fitted to the scene's finished renders |
| `proposal` | With `target_rel_err`: the next `spp`, its `predicted_wall_s`, and the flags `target_met`, `within_budget`, `capped` (held to `max_spp`) and `stepped` (held to 16× the current spp) |
| `notes` | Anything the agent should know, such as a recomputed brightness temperature or non-finite pixels |

The proposal assumes the error falls as 1/√spp, with 20 % headroom. Two things to know about it:
- **It grows at most 16× per step.** In a noisy render the estimated means are noisy too, and that inflates the high percentiles of σ/|μ̂|. In the example above, p95 fell 5.3× between 8 and 128 spp, where 1/√spp predicts 4×. Estimating straight from the 8-spp preview would have asked for 36,074 spp; 19,127 was enough. Each less-noisy step gives a better estimate.
- **The first wall-time prediction is pessimistic.** With one render, all of its time, including fixed setup, is charged to its samples.

### `postprocess(render_id, mode="auto", exposure=None, white=None, component=None, palette="inferno", range_min=None, range_max=None)`

Writes a PNG with [htpp](https://gitlab.com/meso-star/htpp):
- `image` mode tone-maps CIE XYZ, using htpp's `-i` with optional `exposure` and `white`.
- `map` mode colours one of the 8 values per pixel, using htpp's `-m` with a `component` (0–7), a `palette` and an optional range.
- `auto` picks image mode for CIE XYZ, and a map of the radiance (component 2) otherwise.

**Returns:** the PNG and PPM paths, htpp's exact command line, and the size.

## Resources

| URI | Contents |
|---|---|
| `htrdr://renders` | Every render, oldest first: id, scene, spp, status, wall time |
| `htrdr://scenes/{scene_id}` | The scene's spec, warnings and render ids |
| `htrdr://renders/{render_id}` | The render's record: exact command line and argv, status, timings, output tails, and the last stats and postprocess results |

Everything is kept on disk, so state survives a server restart:

```
runs/scenes/<scene_id>/scene.json
runs/renders/<render_id>/render.json     the record behind htrdr://renders/{render_id}
runs/renders/<render_id>/image.txt       htrdr's output, which the browser viewer opens
runs/renders/<render_id>/image.png       after postprocess
runs/cache/<cache_key>.cache             htrdr's -O cache, shared by renders that only change camera, sun, image or spp
```

## Known htrdr issue: monochromatic brightness temperature

In htrdr 0.12, **monochromatic** longwave runs (`range_nm` with equal bounds) report a wrong brightness temperature: it's off by a factor of 10⁹ in radiance. At 10 µm, 0.002256 W/sr/m²/nm comes out as 53 K, while inverting Planck's law gives 229 K. Band runs are consistent, and the radiance is right in both cases.

`read_stats` therefore recomputes the brightness temperature from the radiance for monochromatic runs, and adds a note whenever htrdr's own value disagrees. `postprocess` warns if you map components 0–1 of such a run.

## Development

```bash
uv run pytest
```

```bash
uv run pytest -m htrdr
```

The first runs all tests. The second runs only the tests against the real htrdr, which are skipped when htrdr or its gas data isn't installed. The server tests use FastMCP's in-memory client against the fake htrdr (`fake-htrdr`, in `src/htrdr_agent/fake.py`). The fake writes valid htrdr-images whose noise falls as 1/√spp, and it reproduces the brightness-temperature issue.

| Module | Role |
|---|---|
| `scene.py` | `SceneSpec`, its validation and warnings |
| `argv.py` | htrdr's exact command line |
| `runner.py` | Async subprocess runner: timeout, cancel, progress |
| `image.py` | htrdr-image parser; Planck and brightness temperature |
| `stats.py` | What `read_stats` returns |
| `estimate.py` | The spp and wall-time proposal |
| `store.py` | Scenes and renders on disk |
| `png.py` | PPM → PNG without an imaging library |
| `server.py` | The MCP server |
| `fake.py` | The fake htrdr |
