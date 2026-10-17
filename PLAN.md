# htrdr image viewer — plan

A browser viewer for `htrdr-image` files (TypeScript, WebGL2, Vite). You drag and drop a file and it renders live. It covers everything `htpp` does (image mode and map mode), and it adds a hover readout and A/B comparison.

## 1. What we're reading (verified against the htrdr and htpp sources)

### The htrdr-image format
- The first non-comment line is `width height`. After that come `width × height` lines with one pixel per line.
- Each pixel has **8 reals**: four `(mean, σ)` pairs. `#` starts a comment, and empty lines are ignored. htpp also accepts lines with only 6 values, so the last pair defaults to 0.
- Pixels are stored row-major, starting at the **top-left** corner. htpp writes PPM rows in the same order, without flipping Y.
- The file doesn't say what kind of image it holds. You have to infer that from the htrdr mode that produced it:

| pair | CIE XYZ (default)        | longwave `-s lw=`         | shortwave `-s sw=` |
|------|--------------------------|---------------------------|--------------------|
| 0    | X radiance (W/sr/m²)     | brightness temperature (K)| unused (0)         |
| 1    | Y radiance               | radiance                  | radiance           |
| 2    | Z radiance               | unused (0)                | unused (0)         |
| 3    | time per path (µs)       | time per path (µs)        | time per path (µs) |

  For monochromatic runs, radiance is in W/sr/m²/nm.
- **Layout detection:** if pair 2 is zero everywhere, it's LW when pair 0 is non-zero and SW when pair 0 is zero. Anything else is XYZ. The user can override this in the UI.

### What htpp does (we reproduce it exactly)
**Image mode** applies these steps to each pixel, in this order:
1. Filmic (Uncharted 2) curve on X, Y and Z separately: `T(x) = (x(Ax+CB)+DE)/(x(Ax+B)+DF) − E/F` with A=.15, B=.5, C=.1, D=.2, E=.02, F=.3. Each channel becomes `c' = T(c·exposure) / T(Ymax·exposure)`.
2. Multiply componentwise by the D65 white vector `(x/y, 1, (1−x−y)/y)` with x=0.31271 and y=0.32902. This is an htpp quirk that we keep for parity. Then apply the standard XYZ→linear-sRGB matrix and clamp negatives to 0.
3. Apply the sRGB OETF (12.92 below 0.0031308, otherwise 1.055·x^(1/2.4)−0.055), clamp to [0,1], and quantize with `·255 + 0.5`.
- **Defaults:** `exposure = 1`. `white` (Ymax) is automatic: sort all Y values and take the one at index `floor(N·0.995)`.
- In other words, the "white-point slider" sets **Ymax**, the luminance that maps to white. It isn't a chromaticity.

**Map mode** (`-m pixcpnt=i:palette=p:range=a,b`):
- Picks one of the 8 values per pixel, computes `v = clamp((x − a)/(b − a), 0, 1)`, and samples the palette with linear interpolation.
- When no range is given, it uses the min/max of that component over the image. If `b == a`, the pixel is black.
- The default palette is `inferno`. Palette names come from star-cmap: accent, blues, brbg, bugn, bupu, chromajs, dark2, gnbu, gnpu, greens, greys, inferno, jet, magma, moreland, oranges, orrd, paired, parula, pastel1, pastel2, piyg, plasma, prgn, pubu, pubugn, puor, purd, purples, rdbu, rdgy, rdpu, rdylbu, rdylgn, reds, sand, set1, set2, set3, spectral, viridis, whgnbu, whylrd, ylgn, ylgnbu, ylorbr, ylorrd, ylrd.
- htpp can also write a gnuplot script instead of a PPM.

**Palette licensing:** we don't copy star-cmap's tables. Instead we rebuild the palettes from their permissive upstream sources:
- matplotlib's viridis, magma, inferno and plasma (CC0)
- ColorBrewer (Apache-2.0)
- Moreland's cool-warm (published)
- the analytic formula for jet

parula belongs to MathWorks, so we leave it out or substitute a look-alike, and say so in the UI.

## 2. Architecture

```
File drop ──► parse.worker.ts ──► HtrdrImage { w, h, data: Float32Array(w·h·8), stats, layout }
                (byte tokenizer,        │
                 progress, errors        ├─► GPU: two RGBA32F textures per image
                 with line numbers)      │      tex0 = pairs 0–1, tex1 = pairs 2–3 (NEAREST, texelFetch)
                                         └─► CPU copy kept for hover readout and exports

Renderer (one full-screen triangle; a single fragment shader with uniforms):
  mode: image | map | diff
  view: pan/zoom transform (texel-exact nearest when zoomed in)
  image: exposure, Ymax, htppExact toggle
  map:   component index, range [a,b], log toggle, palette LUT (256×1 RGBA8)
  A/B:   split x (screen space), diff threshold k (default 2)
```

- **Parsing:** runs in a Web Worker over a `Uint8Array`. A hand-written tokenizer splits fields. A fast decimal path handles the common case and falls back to `Number()` on odd input. The worker sends progress events and transfers its buffers back.
- **Stats** are computed in the worker once per image: per-component min/max, the Y p99.5 used for auto-white, and percentiles for robust ranges.
- **Precision:** Float32 is plenty for display. The hover readout formats directly from the stored values.
- **Memory:** a 1920×1080 image takes 66 MB on the CPU plus 66 MB on the GPU. That's fine for two images.
- **Requirements:** WebGL2 with float textures. Sampling RGBA32F with `texelFetch` doesn't need `OES_texture_float_linear`. Exports render into an RGBA8 FBO, so we don't need `EXT_color_buffer_float` either.

### Components (the switcher)
The menu depends on the layout:
- XYZ: X / Y / Z
- LW: brightness temperature / radiance
- SW: radiance

For whichever quantity is selected, you can view:
- **value** (the mean)
- **error** σ
- **relative error** σ/|mean|
- **cost** (time per path, with its σ)

XYZ images default to image mode. LW and SW images default to map mode, because htpp's image mode would treat a temperature as X.

### Hover readout
Shows the pixel `(x, y)` and `value ± σ` with units for the active component, plus the relative error.

In A/B mode it also shows the A and B values, `Δ ± σΔ`, and `z = Δ/σΔ`.

The lookup is a CPU array access, so there's no GPU readback.

### A/B comparison
- There are two slots. Both images must have the same size and layout, and they share the view transform.
- **Split:** a draggable vertical divider. A is on the left and B on the right, with the same mode and settings on both sides.
- **Difference:**
  - `Δ = A − B` and `σΔ = √(σA² + σB²)`. This treats the two Monte Carlo estimates as independent.
  - Where `|Δ| < k·σΔ` (k = 2 by default, adjustable), the pixel shows as neutral gray: the difference isn't significant.
  - Everywhere else, Δ is mapped through a diverging palette (rdbu by default) with a symmetric range of ±max|Δ| or ±a user-set value.
  - An alternative view shows z directly.
  - For XYZ images, the comparison uses the selected component (Y by default).

## 3. Project layout

```
index.html
src/
  main.ts                 app bootstrap, WebGL2 capability check
  format/htrdrImage.ts    types, component indices, layout detection
  format/parse.ts         pure parser core (shared by worker and tests)
  format/parse.worker.ts  worker wrapper: progress + transferables
  format/stats.ts         min/max/percentiles, auto Ymax
  color/tonemap.ts        CPU reference of htpp image mode (exact port)
  color/palettes.ts       palette data + LUT builder (CPU reference of map mode)
  render/gl.ts            context, texture, program helpers
  render/renderer.ts      uniforms, draw, offscreen export
  render/shaders/*.glsl   imported with ?raw
  ui/                     drop zones, controls, readout, split handle, colorbar
tools/gen-fixtures.ts     synthetic htrdr-image files with known structure
fixtures/                 small generated files (XYZ, LW, SW, A/B pair)
test/                     Vitest unit tests
```

The UI is plain TypeScript and DOM with no framework, because the controls are few and mostly sliders.

## 4. Milestones

Dev commands: `pnpm dev`, `pnpm test`, `pnpm e2e`, `pnpm build`, `pnpm fixtures`, `pnpm htpp-parity`, `pnpm tsx tools/gen-large.ts`. This machine uses pnpm because its global npm install is broken.

- **M0 – Foundations** ✅ done 2026-10-16
  - Vite + TS + Vitest scaffold
  - format module and parser core
  - CPU reference ports of htpp's image and map modes
  - fixture generator and fixtures
  - unit tests
- **M1 – Load & show** ✅ done 2026-10-16. A 132 MB 1920×1080 file loads in about 0.8 s.
  - drop zone and worker parser with progress and errors
  - texture upload
  - map-mode grayscale render
  - pan, zoom and fit
- **M2 – Tone mapping** ✅ done 2026-10-16. `/parity.html` runs 96 cases and all pass, with a max Δ of 1 LSB. Note: because htpp divides by `T(white·exposure)`, exposure mostly reshapes the filmic shoulder rather than brightening the image. White is the real brightness control.
  - image-mode shader
  - exposure and Ymax sliders (log scale), auto-white button
  - GPU-vs-CPU-reference check on the fixtures
- **M3 – Components & palettes** ✅ done 2026-10-16. The 42 palettes are generated by `tools/gen-palettes.ts`, and the LUT has 1024 entries so GPU filtering stays within 1 LSB at palette kinks. The parity page covers 192 cases. Still open: whether ColorBrewer's 8-class order matches star-cmap's, which M5 checks against real htpp.
  - component and statistic switcher
  - palette LUTs
  - range controls (auto, percentile, manual, log)
  - colorbar
  - hover readout
- **M4 – A/B** ✅ done 2026-10-16. The parity page now covers 256 cases, including split and difference. The colorbar compares the significant fraction with the noise-only rate erfc(k/√2). The A/B fixtures were regenerated with lower noise, so the boosted square reaches z ≈ 3.4.
  - second slot and compatibility check
  - split slider
  - difference mode with the k·σ gray-out, z view, and diff readout
- **M5 – Export & parity** ✅ done 2026-10-16
  - PNG and P3 PPM export at native resolution, plus a gnuplot script. The PPM and gnuplot formats follow htpp's writers.
  - **Real htpp parity: 97 of 97 cases match htpp 0.5.0.** That covers image mode, map mode for all 8 components across 12 palettes, and one gnuplot script per fixture. 52 cases are byte-exact and the rest are within 1 LSB. ColorBrewer order matches star-cmap's, which closes the M3 question. One fix came out of it: moreland now uses Moreland's float endpoints at 257 samples. With 33 samples it drifted about 4/255 near the warm end.
  - htpp is built from source in WSL Ubuntu into `~/.local` (RSys 0.15, Star-CMap 0.1, htpp 0.5). To rerun from Git Bash:
    `MSYS_NO_PATHCONV=1 HTPP="wsl -d Ubuntu -e env LD_LIBRARY_PATH=/home/nsaulnier/.local/lib /home/nsaulnier/.local/bin/htpp" pnpm htpp-parity`
    `MSYS_NO_PATHCONV=1` stops Git Bash from rewriting the Linux paths. `LD_LIBRARY_PATH` is needed because librsys and libscmap live in `~/.local/lib`.
  - 4K performance (3840×2160, 540 MB file from `tools/gen-large.ts`):
    - load: about 2.6 s, almost all of it parsing in the worker
    - controls: every change under 400 ms
    - exports: PNG 0.1 s, PPM 0.9 s, gnuplot 0.6–1.4 s
    - change: full sorts were replaced with scans and quickselect, which took auto-white from 0.8 s to 0.2 s and ranges from 0.7 s to 0.01–0.09 s
  - Not done: parsing in parallel workers, and parsing straight into the two planes to save about 266 MB of peak worker memory.
- **M6 – Polish** ✅ done 2026-10-16
  - **URL hash:** settings live in the hash (`src/ui/urlState.ts`). The hash is updated quietly as you work. A link's mode, quantity, white and view apply once the first image is dropped, and its compare mode once B is dropped. Invalid values are ignored.
  - **Shortcuts:** F/0 fit, 1 for 1:1, +/− zoom, S swaps A and B (press repeatedly to flip between them), C cycles compare, M toggles image/map, R resets the adjustments, ? opens the help. One table drives both the keys and the help overlay.
  - **Cross-browser:** `pnpm e2e` (Playwright) passes 18 of 18. That's 6 tests in each engine, including the 256-case GPU parity check:
    - Chromium: Chrome for Testing 156
    - Firefox: the installed Firefox 151, driven over WebDriver BiDi. Playwright's own Firefox build won't start on this machine because Windows rejects its mozglue.dll side-by-side assembly.
    - WebKit: Playwright's WebKit 27.2
  - **Not tested in real Safari.** WebKit is Safari's engine, but Safari itself needs macOS.

## 5. Testing
- **Vitest:**
  - parser edge cases: comments, blank lines, 6-value lines, truncated files, bad tokens
  - layout detection
  - stats, including the exact p99.5 index
  - tone map and palette lookup against hand-computed values
- **GPU parity (M2/M5):** render the fixtures into an RGBA8 FBO, `readPixels`, and compare with the CPU reference. The tolerance is ±1 LSB.
- **htpp parity (M5):** run real `htpp` on the fixtures and diff the PPMs.

## 6. Open questions
- Should the viewer also accept other htrdr front-ends' outputs (`htrdr-combustion`, `htrdr-planets`)? They use the same 8-value format but the pairs may mean different things. For now, the layout override covers them.
- How should a NaN or negative mean appear (htrdr can emit these for failed paths)? The plan is a magenta flag color plus a count in the status bar.
