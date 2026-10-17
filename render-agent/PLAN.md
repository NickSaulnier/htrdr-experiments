# htrdr render agent: plan

A FastMCP server that wraps htrdr, and a LangGraph agent that drives it. The agent takes a request such as "IR view at 10 µm, sun at 40° elevation, from 2 km altitude" and turns it into htrdr arguments. It renders a cheap preview, reads the error statistics, re-renders until a target uncertainty is met, and writes a run log with the exact command lines used.

Stack: Python, FastMCP, LangGraph, an OpenAI-compatible LLM endpoint (vLLM serving Qwen), htrdr and htpp.

## 1. Facts this plan rests on (checked 2026-10-17)

### htrdr-atmosphere's CLI

| Need | Flag |
|---|---|
| spectral range | `-s lw=min,max` (thermal emission; output includes brightness temperature), `-s sw=min,max` (solar), or `-s cie_xyz` (the default). Bounds are in nm, and equal bounds mean monochromatic. `Tref=` sets the reference Planck temperature. |
| sun | `-D azimuth,elevation`, in degrees |
| camera | `-C pos=x,y,z:tgt=x,y,z:up=x,y,z:fov=deg` (perspective), or `-P …:height=` (orthographic). Altitude is the z of `pos`; there is no separate flag. |
| flux sensor | `-p pos=…:tgt=…:up=…:sz=w,h` |
| image | `-i def=WxH:spp=N`. CIE XYZ costs 3N samples per pixel. |
| inputs | `-a` gas optical properties (htgop, **required**), `-c` clouds (htcp) with `-m` Mie (htmie), `-g` ground (htrdr-obj) with `-M` materials, `-R`/`-r` to repeat the ground or clouds |
| speed | `-O cache` stores the acceleration structures and reuses them, which makes preview-then-final cheap. `-T` and `-V` tune those structures. `-t` sets threads; MPI works across nodes. |
| output | `-o file -f`. The output is an htrdr-image: 8 values per pixel, four (mean, σ) pairs. Pair 3 is the time per path in µs, which gives the cost. |

**Domain catch:** in `lw` mode htrdr computes thermal emission only, so the sun has no effect. A request for "10 µm with the sun at 40°" is partly contradictory. The agent must flag this: either note that the sun is ignored, or offer an `sw` render where the sun matters.

### Data
- The htgop repository ships `etc.tgz` containing `ecrad_opt_prop.txt`: real gas optical properties from ecRad's k-distribution, for both shortwave and longwave. **That's enough for clear-sky scenes.**
- htmie ships a Mie lookup table as CDL, which `ncgen` turns into NetCDF.
- Cloud fields (htcp) come from LES runs and have to be provided separately.

### Build
- htrdr needs a C compiler with OpenMP, POSIX make, pkg-config, MPI and mandoc.
- Its libraries are RSys, Star-3D (which uses **Embree 4**), Star-Camera, Star-SF, Star-SP, Star-VX, loader_aw, mrumtl, and, for the atmosphere front-end, htsky with htgop, htcp and htmie.
- Ubuntu 22.04 packages only Embree 3. Star-SF also needs RSIMD, which needs SLEEF ≥ 3.6, and apt only has 3.5.1.
- RSys, Star-CMap and htpp are already built into `~/.local` in WSL Ubuntu.

### Machine
- WSL Ubuntu 22.04 has an RTX 4090 (24 GB) and Python 3.10. Windows has Python 3.11–3.14 and `uv`.
- **vLLM and Qwen are not on this machine.** Only Ollama (`llama3.1`) is running, on :11434.

## 2. Architecture

```
            ┌──────────── WSL Ubuntu (RTX 4090) ─────────────┐
 request →  │ LangGraph agent ──MCP (stdio)──► FastMCP server │
            │      │                            │ build_scene │
            │      ▼ OpenAI API                 │ render      │──► htrdr-atmosphere (~/.local)
            │  vLLM · Qwen (tool calling)       │ postprocess │──► htpp
            │                                   │ read_stats  │──► numpy parser
            └──────────────────────────────────────────────────┘
                         runs/<run-id>/  (in the repo on D:, git-ignored, so the viewer opens them)
```

- **Everything runs in WSL:** htrdr, the server, the agent and vLLM. That avoids path translation and gives vLLM the GPU.
- **Project:** a `uv` project in `render-agent/`, on Python 3.12.
- **Principle: numbers in tools, judgment in the LLM.** Tools compute errors, spp estimates and time estimates exactly. The LLM turns the request into a spec and chooses among options the tools offer. Code enforces budgets, caps and iteration limits.

## 3. MCP tools

| Tool | Input | Returns |
|---|---|---|
| `build_scene` | a typed `SceneSpec`: spectral mode and range, sun, camera or flux sensor, resolution, input files | `scene_id`, the exact argv, warnings (sun ignored in `lw`, camera under the ground, target out of view), and a cache path keyed by the inputs |
| `render` | `scene_id`, `spp`, optional resolution, threads, timeout | `render_id`, output path, `shlex.join(argv)`, wall time, exit code, stderr tail. Reports progress, can be cancelled, and is killed on timeout. |
| `postprocess` | `render_id`, htpp options | the path of a PNG/PPM made by htpp, for people and the viewer |
| `read_stats` | `render_id`, optional `target_rel_err` and region | summaries only, never pixels (below) |

`read_stats` returns:
- Per-quantity mean and range.
- The relative-error distribution: median, p90, p95, p99, max.
- The fraction of pixels meeting the target, and the worst tiles on an 8×8 grid.
- NaN counts.
- Cost: mean time per path, and estimated CPU time.
- The extrapolation `spp_needed = ⌈spp · (e_p95 / e_target)² · safety⌉`, from σ ∝ 1/√N, with its projected wall time.

The parser ports the viewer's tested logic to numpy and reuses the viewer's fixtures in its tests.

## 4. Agent

```
parse_request ─► build_scene ─► preview (low spp) ─► read_stats ─► decide
                                     ▲                               │
                                     └── re-render / new camera ◄────┤
                                                                     ├─► done (target met)  ─► write_run_log
                                                                     └─► stop (over budget) ─► write_run_log
```

1. **`parse_request`:** the LLM produces a `SceneSpec` using JSON-schema guided decoding, so the output always parses. Every inferred value (view direction, azimuth, field of view) is recorded as an assumption. If something essential is ambiguous, the graph asks for clarification through `interrupt()`.
2. **Preview:** rendered at the final resolution and low spp, so its per-pixel errors extrapolate correctly.
3. **`decide`:**
   - The LLM picks from options that code computes: re-render at N spp, change the camera, or stop. It must give a reason.
   - A camera change applies when the stats contradict the intent. Examples: brightness temperatures near space temperature (the view is looking at sky), NaNs, or degenerate framing.
4. **Stop rule (in code):** stop when the target is met, by default p95 relative error ≤ 1 %. Otherwise stop when the wall-time, spp or iteration budget runs out, and say so.
   - A render projected to exceed a time threshold pauses for human approval.
   - A SQLite checkpointer lets interrupted runs resume.
5. **Run log:** `runs/<id>/run.json` and `run.md`, containing:
   - the request, the spec and the assumptions
   - every exact command line
   - htrdr and htpp versions, and hashes of the input files
   - timings, stats for each iteration, and each decision with its reason
   - the output paths

## 5. Milestones

- **M0 – htrdr in WSL** ✅ done 2026-10-17
  - **Build:** `scripts/build-htrdr.sh` builds SLEEF 3.6.1 and Embree 4.3.3 from source, then 14 pinned meso-star libraries ending in htrdr 0.12 (atmosphere only), all into `~/.local`, with no root. The script lists the apt packages it needs.
  - **Data:** `scripts/fetch-data.sh` fetches htgop's gas data. It's a Git LFS object; the script checks its SHA-256.
  - **Smoke tests:** clear-sky 10 µm (64×48, 16 spp) in 0.42 s, and CIE XYZ with the sun at 40° (160×120, 32 spp) in 0.52 s. The second has a mean relative error of 41 %, so reaching 1 % would need about 54k spp, roughly 15 minutes.
  - **htrdr bug found:** in **monochromatic** `lw` runs (`lw=x,x`), the brightness temperature in pair 0 is off by a factor of 10⁹ in radiance. 0.002256 W/sr/m²/nm at 10 µm is reported as 53 K; inverting Planck gives 229 K. Band runs (`lw=9500,10500`) are consistent, and the radiance is right in both. **So `read_stats` must recompute Tb from the radiance for monochromatic runs.** Worth reporting upstream.
  - Band runs are noisier than monochromatic ones (about 10 % vs 2 % relative error at 16 spp), because they also sample the k-distribution.
- **M1 – Python core:**
  - `SceneSpec`, an argv builder with golden tests, and a subprocess runner with timeout and cancel.
  - The htrdr-image parser, stats, and the spp/time estimator.
  - A fake htrdr producing synthetic images with σ ∝ 1/√spp, so the loop can be tested without htrdr.
- **M2 – FastMCP server:** the four tools, plus run logs as resources. Tested with FastMCP's in-memory client.
- **M3 – LLM:** endpoint config and request parsing, with an eval set of about 20 requests mapped to expected specs (including the 10 µm-plus-sun conflict).
- **M4 – LangGraph loop:** guardrails, interrupts and checkpointing. Run end to end on the fake htrdr, then on the real one.
- **M5 – Run logs and integration:** a real-htrdr run of the example request, with its output opened in the viewer.

## 6. Open questions
1. Where should vLLM and Qwen run: another host, or set up in WSL during M3? On a 24 GB 4090, a 14B Qwen (AWQ) is comfortable and a 32B one is tight.
2. Is clear sky enough to start, or should LES clouds and ground meshes be supported from the beginning?
3. Is p95 relative error ≤ 1 % the right default target, and what wall-time budget should one run get?
