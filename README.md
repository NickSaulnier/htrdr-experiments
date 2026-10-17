# htrdr image viewer

A browser viewer for [htrdr](https://www.meso-star.com/projects/htrdr/htrdr.html) output images. You drag in an `htrdr-image` file and it renders live on the GPU. It does what [htpp](https://gitlab.com/meso-star/htpp) does, interactively, and adds per-pixel error readouts and A/B comparison.

Built with TypeScript, WebGL2 and Vite. Files never leave your machine: they are parsed in a Web Worker and drawn locally.

## Features

- **Image mode:** htpp's tone mapping in a fragment shader: its filmic curve, its XYZ → sRGB conversion and sRGB encoding. Exposure and white (Ymax) are sliders, and htpp's automatic white (the Y at 99.5 % of pixels) is the default.
- **Map mode:** any quantity in the file, shown as its value, standard error σ, relative error σ/|μ|, or the computation cost per path.
  - 42 palettes from htpp's list.
  - Min–max, percentile or manual ranges, with an optional log scale.
  - A colorbar.
- **Hover readout:** `value ± σ` with units and relative error, rounded to the precision the estimate supports, plus the cost of the pixel.
- **A/B comparison:**
  - **Split:** a draggable divider.
  - **Difference:** Δ = A − B, with pixels grayed out where |Δ| < k·σΔ, σΔ = √(σA² + σB²), and k = 2 by default.
  - **z view:** shows z = Δ/σΔ instead.
  - The legend compares the share of significant pixels with what noise alone would produce.
- **Exports** at native resolution:
  - PNG
  - PPM, as htpp writes it
  - a gnuplot script, in the layout of htpp's `-m …:gnuplot`
- **Shareable views:** settings are kept in the URL hash. A bookmark or link reopens the same view once the same files are dropped in.
- **Large images:** a 3840×2160 file (540 MB of text) loads in about 2.6 s, and every control responds in under 400 ms.

## Getting started

You need a recent Node.js (developed with Node 24) and [pnpm](https://pnpm.io). The lockfile is pnpm's.

```bash
corepack enable
```

```bash
pnpm install
```

```bash
pnpm dev
```

Then open the URL Vite prints, and drop an `htrdr-image` file onto the page or use **Open…**. A few synthetic test images are in [`fixtures/`](fixtures).

The viewer needs WebGL2, which current Chrome, Edge, Firefox and Safari all provide.

## Using it

**Two images:** drop two files at once, or once an image is open, drop a file on the right half of the window or use **Open B…**. B must have the same size and spectral layout as A.

**Keyboard shortcuts** (press `?` in the app for the list):

| Key | Action |
|---|---|
| `F` / `0` | Fit the image (also double-click) |
| `1` | Zoom to 1:1 |
| `+` / `−` | Zoom in / out |
| `S` | Swap A and B (press repeatedly to flip between them) |
| `C` | Cycle compare: off, split, difference |
| `M` | Toggle image and map mode |
| `R` | Reset exposure, white, ranges and thresholds |
| `?` | Show or hide the help |

Drag to pan and use the mouse wheel to zoom. Non-finite pixels (NaN or infinity) are shown in magenta, and the status bar counts them.

## The htrdr-image format

An `htrdr-image` file (see `htrdr-image(5)`) is plain text:
- The first line is `width height`.
- Then come `width × height` lines, one per pixel, in row-major order from the top-left.
- Each pixel line has 8 numbers: four (mean, standard error) pairs.
- `#` starts a comment, and blank lines are ignored.

What the pairs mean depends on the htrdr mode that wrote the file, which the file doesn't record:

| Pair | CIE XYZ (default) | Longwave (`-s lw=`) | Shortwave (`-s sw=`) |
|---|---|---|---|
| 0 | X radiance (W/sr/m²) | brightness temperature (K) | unused |
| 1 | Y radiance | radiance | radiance |
| 2 | Z radiance | unused | unused |
| 3 | time per path (µs) | time per path (µs) | time per path (µs) |

The viewer guesses the layout from which pairs are zero. Image mode is offered only for CIE XYZ images.

## How it's checked against htpp

The viewer is meant to produce the same pixels htpp does, so several layers of checks back it up:

- **CPU reference:** `src/color/tonemap.ts` and `src/color/palettes.ts` port htpp's image and map modes line by line.
- **Real htpp:** `pnpm htpp-parity` runs htpp on every fixture and compares its PPM and gnuplot output with that reference. It covers image mode, all 8 components across 12 palettes, and automatic and explicit ranges. Against htpp 0.5.0, all 97 cases match: 52 byte for byte, the rest within one 8-bit step.
- **GPU vs CPU:** `/parity.html` (open it on the dev server) renders 256 cases on the GPU and compares them byte for byte with the reference, covering split and difference modes too.
- **Browsers:** `pnpm e2e` runs the app and the GPU check in Chromium, Firefox and WebKit.

To run the htpp check, build [RSys](https://gitlab.com/vaplv/rsys), [Star-CMap](https://gitlab.com/meso-star/star-cmap) and [htpp](https://gitlab.com/meso-star/htpp) with `make install PREFIX=…`, then point `HTPP` at the binary. If htpp lives in WSL, paths are translated automatically when the command starts with `wsl`:

```bash
HTPP="wsl -d Ubuntu -e env LD_LIBRARY_PATH=/home/<user>/.local/lib /home/<user>/.local/bin/htpp" pnpm htpp-parity
```

From Git Bash, also set `MSYS_NO_PATHCONV=1` so the Linux paths aren't rewritten.

## Development

| Command | What it does |
|---|---|
| `pnpm dev` | Dev server with hot reload |
| `pnpm build` | Type-check and build into `dist/` |
| `pnpm test` | Unit tests (Vitest) |
| `pnpm e2e` | End-to-end tests in Chromium, Firefox and WebKit (Playwright) |
| `pnpm fixtures` | Regenerate the synthetic images in `fixtures/` |
| `pnpm htpp-parity` | Compare against a real htpp (see above) |
| `pnpm tsx tools/gen-large.ts` | Write a 3840×2160 test image to `fixtures/large/` (git-ignored) |

Before running `pnpm e2e`, install the browsers:

```bash
pnpm exec playwright install chromium webkit
```

The Firefox project drives your installed Firefox over WebDriver BiDi (channel `moz-firefox`), so Firefox needs to be installed normally. The WebKit project tests Safari's engine, not Safari itself.

### Layout

```
src/format/   parsing, the image model, statistics, differences
src/color/    tone mapping and palettes (the CPU reference of htpp)
src/render/   WebGL2 renderer, shaders, view transform, ranges, parity reference
src/export/   PNG, PPM and gnuplot writers
src/ui/       drop zone, controls, colorbar, shortcuts, URL hash
tools/        fixture, palette and large-image generators; the htpp parity script
test/         unit tests       e2e/   Playwright tests
```

[`PLAN.md`](PLAN.md) has the design notes and the milestone history.

## Palettes and licensing

The viewer offers the palettes in htpp's list (via star-cmap), rebuilt from their original, permissively licensed sources rather than copied from star-cmap:
- **viridis, magma, inferno, plasma:** Nathaniel J. Smith, Stefan van der Walt and Eric Firing, CC0.
- **ColorBrewer schemes:** © 2002 Cynthia Brewer, Mark Harrower and The Pennsylvania State University, Apache License 2.0.
- **moreland:** computed from Kenneth Moreland's diverging color map algorithm.
- **jet:** computed from its definition.

`chromajs`, `gnpu`, `parula`, `sand`, `whgnbu`, `whylrd` and `ylrd` are not included, because their tables have no clear license; parula belongs to MathWorks.

The viewer itself is released under the [MIT License](LICENSE).
