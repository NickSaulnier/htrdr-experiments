# htrdr render agent

Ask for a render in plain words, such as *"IR view at 10 µm, sun at 40° elevation, from 2 km altitude"*. The agent renders it with [htrdr](https://www.meso-star.com/projects/htrdr/htrdr.html), the Monte Carlo radiative transfer renderer, and keeps refining until the image meets a target uncertainty. Then it writes a run log with the exact command lines and links that open the result in the [browser viewer](../README.md).

It has two parts:
- **`htrdr-agent`**, a [LangGraph](https://langchain-ai.github.io/langgraph/) agent. A local LLM (Qwen on [vLLM](https://docs.vllm.ai)) reads the request and chooses between options that code has already checked.
- **`htrdr-mcp`**, an [MCP](https://modelcontextprotocol.io) server built with [FastMCP](https://gofastmcp.com). It wraps htrdr as four tools. The agent uses it, and so can any other MCP client.

The agent never handles pixels. It works from summaries of each render's per-pixel error and cost, plus a proposal for the next render: the samples per pixel (spp) the target needs and how long that should take. The design and milestone history are in [`PLAN.md`](PLAN.md).

## What a run looks like

```
$ uv run htrdr-agent --runs-dir ../runs "IR view at 10 µm, sun at 40° elevation, from 2 km altitude"
parsed: lw 10000-10000 nm, camera at 2000 m, target 1 %, budget 1800 s
  warning: The sun has no effect in lw mode: htrdr computes thermal emission only. ...
rendered 8 spp in 1.04 s (ok)
  p95 61.3 %, p50 29.1 %
decide: render 128 spp (~1.3 s) [llm]
  p95 61.3 % vs target 1 % after 8 spp; 1799 s of budget left; next: 128 spp, predicted 1.3 s.
  The current uncertainty is far above the target ...
...
rendered 19165 spp in 42.06 s (ok)
  p95 0.917 %, p50 0.675 %
decide: stop [rule]
  Target met: p95 0.009166 ≤ 0.01.
```

That real run, at the default 320×240:

| spp | wall time | predicted | p95 relative error |
|---|---|---|---|
| 8 | 1.04 s | 1.03 s | 61.3 % |
| 128 | 1.23 s | 1.29 s | 11.6 % |
| 2,048 | 5.38 s | 5.37 s | 2.79 % |
| 19,165 | 42.1 s | 41.9 s | **0.917 %** (target met) |

## Setup

Everything runs in Linux; on Windows that means WSL (developed on Ubuntu 22.04, with an RTX 4090).

**1. Build htrdr into `~/.local`.** First install the system packages (this is the only step that needs `sudo`):

```bash
sudo apt-get install -y build-essential pkg-config git libopenmpi-dev openmpi-bin libnetcdf-dev netcdf-bin librandom123-dev libtbb-dev cmake mandoc
```

Then build. This takes a few minutes, most of it compiling Embree:

```bash
bash render-agent/scripts/build-htrdr.sh
```

htrdr has no version option, so the script also writes a build manifest (`~/.local/share/htrdr-build/manifest.json`) for run logs.

**2. Fetch the gas optical properties** that ship with htgop. That's enough for clear-sky scenes. The script checks the download's SHA-256.

```bash
bash render-agent/scripts/fetch-data.sh
```

The file lands at `~/htrdr-data/etc/ecrad_opt_prop.txt`.

**3. Install the Python project** with [uv](https://docs.astral.sh/uv/) (Python 3.12):

```bash
cd render-agent && uv sync
```

**4. Serve the LLM.** Install vLLM into its own environment (PyTorch is several GB, so it stays out of this project), then start it:

```bash
uv venv ~/venvs/vllm --python 3.12
```

```bash
VIRTUAL_ENV=~/venvs/vllm uv pip install vllm==0.31.0 --torch-backend=auto
```

```bash
bash render-agent/scripts/serve-llm.sh
```

This serves Qwen/Qwen3.5-9B, quantized to FP8 on load (about 10 GB of VRAM), on `http://127.0.0.1:8000/v1` as model `qwen`. The first start downloads about 18 GB of weights. Any other OpenAI-compatible endpoint works too: set `HTRDR_LLM_URL`, `HTRDR_LLM_MODEL` and `HTRDR_LLM_KEY`. Without an LLM, `--rules` still decides between renders, but parsing a request needs the model.

## Using the agent

```bash
uv run htrdr-agent --runs-dir ../runs "A colour photo of the sky from 2 km looking 10 degrees above the horizon, sun at 30 degrees, 3% uncertainty"
```

| Option | Meaning |
|---|---|
| `--approve-over S` | Ask before renders predicted to take longer than S seconds (default 120; negative never asks) |
| `--yes` | Approve those renders without asking |
| `--resume RUN_ID` | Continue a checkpointed run, for example after an interruption or a declined approval |
| `--rules` | Decide with the rules only, without the LLM |
| `--max-iterations N` | Renders per run, preview included (default 8) |
| `--fake` | Use the fake htrdr: no real renders, for trying things out |
| `--runs-dir DIR` | Where scenes, renders, checkpoints and run logs go (default `./runs`) |

**How a request is read.** The LLM reports what the request says: which kind of light it names, any wavelength numbers, words about quality, and whether it's a render request at all. Code applies the conventions:
- **Spectral mode:** a wavelength of 4 µm or more renders as thermal emission (htrdr `lw`); below that, as sunlight (`sw`), even if the request calls it infrared. "Infrared" with no number means the 8–12 µm window. With no light words, the default is a visible colour image.
- **Target:** a stated percentage wins; otherwise "quick" means 5 %, "high quality" 0.5 %, and the default is 1 %. The target applies to the p95 of the per-pixel relative error of Y (visible) or the radiance.
- **Budget:** a stated time limit, or 30 minutes of rendering.
- **Camera:** placed at the requested altitude, looking along the requested elevation and heading. Every default the model picks is listed as an assumption in the run log.

A message that isn't a render request (a question, a thank-you) renders nothing.

**How it decides.** After each render, `read_stats` proposes the next spp from the measured error and a cost model fitted to the scene's renders. Code then lists what's allowed:
- the proposal, if it fits the remaining budget;
- otherwise, the largest render the budget still affords;
- and always, stopping.

The LLM picks one and says why, and its choice is constrained to those options. Reaching the target stops the run without asking the LLM. If the LLM's answer is invalid or the call fails, the rule's choice is used, and the log says so.

**Run logs.** Each run writes `runs/agent/<run_id>/run.md`, a readable report, and `run.json`, the full state. The report covers:
- the request and how it was read;
- scene warnings;
- every render with its timings, prediction and error;
- the **exact command lines**;
- each decision with **its facts written by code**, the reason, and who made it (rule, LLM or person);
- how to reproduce the final image (htrdr and htpp command lines);
- the environment: htrdr's build manifest and binary SHA-256, htpp's version, the agent's commit, the LLM, and each input file's SHA-256.

**Opening results in the viewer.** With the viewer's dev server running (`pnpm dev` in the repository root), each run log links to:
- **the final image**, with display settings that suit it (for example a radiance map for monochromatic IR, given the brightness-temperature issue below);
- **the final render against the previous one**, in z-difference mode. For two renders of the same scene, about 5 % of pixels should differ beyond 2σ from noise alone; much more means something changed between them.

The viewer opens these through `#a=` / `#b=` in its URL. Set `HTRDR_VIEWER_URL` if the viewer isn't at `http://localhost:5173/`.

## The MCP server

The agent starts the server in-process. Other clients can run it over stdio:

```bash
uv run htrdr-mcp --runs-dir ../runs
```

| Option | Meaning |
|---|---|
| `--runs-dir DIR` | Where scenes, renders and caches are kept (default `$HTRDR_RUNS`, else `./runs`) |
| `--prefix DIR` | htrdr's install prefix (default `$HTRDR_PREFIX`, else `~/.local`) |
| `--threads N` | Passed to htrdr as `-t` (default: all cores) |
| `--fake` | Use the fake htrdr: same command line, synthetic images, no real renders |

htrdr's shared libraries are found from the prefix automatically, and htpp is used for `postprocess` when it's installed in the same prefix. FastMCP prints a startup banner on stderr; stdout carries only the protocol. To register the server with an MCP client inside WSL:

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

### Tools

**`build_scene(spec)`** registers a scene. The same spec always gives the same `scene_id`. The server checks that every input file exists and records its SHA-256.

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

It returns the `scene_id`, `warnings`, `cache_key`, `input_sha256`, and a `command_template`, the htrdr command line every render will run.

**Read the warnings.** They flag specs htrdr accepts that probably aren't what was meant:
- A sun in `lw` mode has no effect, because htrdr computes thermal emission only.
- `sw` or `cie_xyz` without a sun uses htrdr's default sun.
- A downward view without a ground only sees the atmosphere below the horizon.
- Monochromatic `lw` triggers the brightness-temperature issue below.

**`render(scene_id, spp, timeout_s=None)`** runs htrdr once and waits for it, reporting htrdr's own progress as MCP progress notifications. Renders run one at a time, since htrdr already uses every core.

It returns:
- `render_id`, `status` (`ok`, `failed`, `timeout` or `cancelled`), `wall_s`
- `predicted_wall_s`, `command_line`, `output` and a `message`
- `retries`
- `stderr_tail` when the render didn't succeed

A failed or timed-out render comes back with that status, not as an error; a bad `scene_id` or `spp` is a tool error. If the client cancels the call, htrdr is stopped. The default timeout is one hour.

**`read_stats(render_id, target_rel_err=None, percentile=95, max_spp=None, budget_s=None)`** returns error and cost summaries of a finished render, never its pixels.

| Key | Contents |
|---|---|
| `quantities` | Mean, min and max of each quantity: X, Y, Z for CIE XYZ; brightness temperature and radiance for `lw`; radiance for `sw` |
| `error` | Per-pixel relative error σ/\|μ\| of **Y** (CIE XYZ) or the **radiance** (`lw`, `sw`): `percentiles` (p50, p90, p95, p99), `max`, `fraction_meeting_target`, and the 5 `worst_tiles` on an 8×8 grid |
| `cost` | `mean_time_per_path_us`, `paths`, and `cpu_s`, the CPU time spent |
| `cost_model` | Wall time ≈ `fixed_s` + `per_spp_s` × spp, fitted to the scene's finished renders |
| `proposal` | With `target_rel_err`: the next `spp`, its `predicted_wall_s`, and the flags `target_met`, `within_budget`, `capped` (held to `max_spp`) and `stepped` (held to 16× the current spp) |
| `notes` | Anything worth knowing, such as a recomputed brightness temperature or non-finite pixels |

The proposal assumes the error falls as 1/√spp, with 20 % headroom. It **grows at most 16× per step**: in a noisy render the estimated means are noisy too, which inflates the high percentiles of σ/|μ̂|. In the example above, p95 fell 5.3× between 8 and 128 spp, where 1/√spp predicts 4×. Estimating straight from the 8-spp preview would have asked for 36,074 spp, and about 19,000 was enough. The first wall-time prediction is pessimistic, since a lone render's setup time is charged to its samples.

**`postprocess(render_id, mode="auto", exposure=None, white=None, component=None, palette="inferno", range_min=None, range_max=None)`** writes a PNG with [htpp](https://gitlab.com/meso-star/htpp):
- `image` mode tone-maps CIE XYZ (htpp's `-i`).
- `map` mode colours one of the 8 values per pixel (htpp's `-m`).
- `auto` picks image mode for CIE XYZ and a radiance map otherwise.

It returns the PNG and PPM paths and htpp's exact command line.

### Resources

| URI | Contents |
|---|---|
| `htrdr://info` | What renders run with: htrdr's build manifest and binary SHA-256, htpp's version |
| `htrdr://renders` | Every render, oldest first: id, scene, spp, status, wall time |
| `htrdr://scenes/{scene_id}` | The scene's spec, warnings, input hashes and render ids |
| `htrdr://renders/{render_id}` | The render's record: exact command line and argv, status, timings, output tails, failed attempts, and the last stats and postprocess results |

Everything is kept on disk, so state survives a restart:

```
runs/scenes/<scene_id>/scene.json
runs/renders/<render_id>/render.json     the record behind htrdr://renders/{render_id}
runs/renders/<render_id>/image.txt       htrdr's output, which the viewer opens
runs/renders/<render_id>/image.png       after postprocess
runs/cache/<cache_key>.cache             htrdr's -O cache (htrdr didn't write one for clear-sky scenes here)
runs/agent/<run_id>/run.md, run.json     the agent's run logs
runs/agent/checkpoints.sqlite            the agent's checkpoints
```

## Known htrdr 0.12 issues

**Monochromatic brightness temperature.** Monochromatic longwave runs (`range_nm` with equal bounds) report a brightness temperature that's off by a factor of 10⁹ in radiance. At 10 µm, 0.002256 W/sr/m²/nm comes out as 53 K, while inverting Planck's law gives 229 K. Band runs are consistent, and the radiance is right in both cases. `read_stats` recomputes the brightness temperature from the radiance for monochromatic runs and notes when htrdr's own value disagrees. The viewer links show the radiance for such runs.

**SIGFPE on many threads.** A visible-light render (320×240 at 2,048 spp, about 470M paths) died of SIGFPE inside Star-SF's sampling. It crashed at 30, 31 and 32 threads and succeeded at 16 and 1. The time to crash varied, so the root cause isn't established; it could be a numerical edge case or a race. On SIGFPE, `render` retries once with half the threads and records both attempts. In the run above, the retried render agreed with the next one statistically (4.7 % of pixels beyond 2σ, against 4.6 % expected from noise).

## Evaluating the request parser

```bash
uv run htrdr-eval
```

```bash
uv run htrdr-eval --cases evals/holdout2.jsonl --repeat 3
```

These score the parser against cases in `evals/`, each a request with expected fields matched exactly, approximately or by range. Results, with every raw reply, go to `runs/evals/`. All 36 cases pass over three repeats. Both holdout sets have influenced the design since they were written, though, so the last unseen score was 6 of 8 cases. A fresh holdout is needed before claiming more. Qwen's thinking mode is off: at temperature 0, its reasoning loops until it runs out of tokens.

## Development

```bash
uv run pytest
```

```bash
uv run pytest -m htrdr
```

The first runs all tests: 113 here, including 2 against the real htrdr and 1 against the LLM endpoint, which are skipped when those aren't available. The second runs only the real-htrdr tests.

The fake htrdr (`fake-htrdr`) takes the same command line and writes valid htrdr-images whose noise falls as 1/√spp. It reproduces the brightness-temperature issue, and it can simulate failures, slowness and SIGFPE. The agent tests drive the whole graph through the real MCP server with a stub LLM, covering approvals, resuming in a new process, a rogue or failing LLM, budgets and iteration limits.

| Module | Role |
|---|---|
| `agent.py` | The LangGraph agent: nodes, guardrails, approvals |
| `cli.py` | `htrdr-agent` |
| `request.py` | What the LLM fills in, and the code that turns it into a scene |
| `llm.py` | Request parsing with JSON-schema guided decoding |
| `evaluate.py` | `htrdr-eval` |
| `runlog.py` | The run logs and viewer links |
| `server.py` | The MCP server (`htrdr-mcp`) |
| `store.py` | Scenes and renders on disk |
| `scene.py` | `SceneSpec`, its validation and warnings |
| `argv.py` | htrdr's exact command line |
| `runner.py` | Async subprocess runner (timeout, cancel, progress) and install info |
| `image.py` | htrdr-image parser; Planck and brightness temperature |
| `stats.py` | What `read_stats` returns |
| `estimate.py` | The spp and wall-time proposal |
| `png.py` | PPM → PNG without an imaging library |
| `fake.py` | The fake htrdr |
