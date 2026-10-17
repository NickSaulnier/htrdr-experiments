// App entry: wires the drop zone, parse worker, renderer, controls and pan/zoom.

import { DEFAULT_PALETTE, PALETTES, bakeLut, paletteByName } from "./color/palettes";
import { STATISTICS, derivedValues, type Statistic } from "./format/derived";
import { diffAt, incompatibility, noiseOnlyFraction, summarizeDiff, type DiffSummary, type DiffView } from "./format/diff";
import { TIME_PAIR, meanIndex, quantities, sigmaIndex, type Layout, type Pair, type Quantity } from "./format/htrdrImage";
import { LoadError, loadImageFile, type LoadJob } from "./format/load";
import { pixelValue, type LoadedImage } from "./format/loadedImage";
import { downloadBlob, pngBlob, stem } from "./export/download";
import { gnuplotChunks, ppmChunks } from "./export/formats";
import { DEFAULT_RANGE, rangeOf, type RangeMode, type RangeSpec } from "./render/range";
import { Renderer, type CompareMode, type DisplayMode, type RenderSettings } from "./render/renderer";
import { canvasToImage, fitView, pixelAt, zoomAt, type View } from "./render/view";
import { Colorbar } from "./ui/colorbar";
import { attachDropZone } from "./ui/dropZone";
import { formatEstimate, formatNumber, formatPercent } from "./ui/format";
import { bindLogControl, formatValue } from "./ui/logControl";
import { attachShortcuts, renderHelp, type ShortcutAction } from "./ui/shortcuts";
import { attachSplitHandle, placeSplitHandle } from "./ui/splitHandle";
import { decodeSettings, encodeSettings, type UrlSettings } from "./ui/urlState";
import { attachViewportControls } from "./ui/viewportControls";

const $ = <T extends HTMLElement>(id: string) => document.getElementById(id) as T;
const stage = $("stage");
const canvas = $<HTMLCanvasElement>("view");
const fileInputs = [$<HTMLInputElement>("file-input"), $<HTMLInputElement>("file-input-b")] as const;
const fileInfo = [$("file-info"), $("file-info-b")] as const;
const openBButton = $<HTMLButtonElement>("open-b");
const compareSelect = $<HTMLSelectElement>("compare");
const swapButton = $<HTMLButtonElement>("swap");
const exportMenu = $<HTMLDetailsElement>("export-menu");
const gnuplotButton = exportMenu.querySelector<HTMLButtonElement>('[data-export="gnuplot"]')!;
const controlsRow = $("controls-row");
const modeField = $("mode-field");
const modeSelect = $<HTMLSelectElement>("mode");
const imageControls = $("image-controls");
const whiteAutoButton = $<HTMLButtonElement>("white-auto");
const mapControls = $("map-controls");
const quantitySelect = $<HTMLSelectElement>("quantity");
const statisticSelect = $<HTMLSelectElement>("statistic");
const paletteSelect = $<HTMLSelectElement>("palette");
const rangeModeSelect = $<HTMLSelectElement>("range-mode");
const pctFields = $("pct-fields");
const pctLow = $<HTMLInputElement>("pct-low");
const pctHigh = $<HTMLInputElement>("pct-high");
const rangeMin = $<HTMLInputElement>("range-min");
const rangeMax = $<HTMLInputElement>("range-max");
const logCheckbox = $<HTMLInputElement>("log");
const diffControls = $("diff-controls");
const diffQuantitySelect = $<HTMLSelectElement>("diff-quantity");
const diffViewSelect = $<HTMLSelectElement>("diff-view");
const diffK = $<HTMLInputElement>("diff-k");
const diffPaletteSelect = $<HTMLSelectElement>("diff-palette");
const diffRangeField = $<HTMLInputElement>("diff-range");
const diffRangeAuto = $<HTMLButtonElement>("diff-range-auto");
const splitHandle = $("split");
const fitButton = $<HTMLButtonElement>("fit");
const actualButton = $<HTMLButtonElement>("actual");
const zoomLabel = $("zoom");
const empty = $("empty");
const statusText = $("status");
const progress = $<HTMLProgressElement>("progress");
const cursorText = $("cursor");
const colorbar = new Colorbar($("colorbar"));

const LAYOUT_LABELS: Record<Layout, string> = { xyz: "CIE XYZ", longwave: "longwave", shortwave: "shortwave" };
/** Quantity shown first for each layout: Y, brightness temperature, radiance. */
const DEFAULT_PAIR: Record<Layout, Pair> = { xyz: 1, longwave: 0, shortwave: 1 };
const DEFAULT_DIFF_PALETTE = "moreland";

function setStatus(text: string, isError = false): void {
  statusText.textContent = text;
  statusText.classList.toggle("error", isError);
}

let renderer: Renderer;
try {
  renderer = new Renderer(canvas);
} catch (err) {
  setStatus(err instanceof Error ? err.message : String(err), true);
  throw err;
}

interface Slot {
  image: LoadedImage;
  name: string;
  /** The same-origin path it was fetched from, when it came from a link (not a drop). */
  source?: string;
}

const state = {
  a: null as Slot | null,
  b: null as Slot | null,
  jobs: [null, null] as [LoadJob | null, LoadJob | null],
  view: null as View | null,
  /** Refit on resize until the user pans or zooms. */
  autoFit: true,
  mode: "map" as DisplayMode,
  /** Kept across images, so two renders can be compared at one exposure. */
  exposure: 1,
  /** null means htpp's automatic white for image A. */
  white: null as number | null,
  pair: 1 as Pair,
  stat: "value" as Statistic,
  palette: DEFAULT_PALETTE,
  lut: new Uint8Array() as Uint8Array,
  /** Kept across images, like exposure. */
  range: { ...DEFAULT_RANGE } as RangeSpec,
  compare: {
    mode: "none" as CompareMode,
    /** Split position as a fraction of the stage width. */
    split: 0.5,
    k: 2,
    view: "delta" as DiffView,
    palette: DEFAULT_DIFF_PALETTE,
    lut: new Uint8Array() as Uint8Array,
    /** null means the automatic range. */
    range: null as number | null,
  },
  hover: null as [number, number] | null,
};

// --- Derived values -------------------------------------------------------

/** A's derived values per (pair, statistic), and the ranges resolved from them. */
const valueCache = new Map<string, Float32Array>();
const VALUE_CACHE_SIZE = 6;
const rangeCache = new Map<string, [number, number]>();
/** Difference summaries per (pair, k). */
const diffCache = new Map<string, DiffSummary>();

function clearCaches(): void {
  valueCache.clear();
  rangeCache.clear();
  diffCache.clear();
}

function currentValues(): Float32Array {
  const key = `${state.pair}:${state.stat}`;
  let values = valueCache.get(key);
  if (!values) {
    values = state.a ? derivedValues(state.a.image, state.pair, state.stat) : new Float32Array();
    if (valueCache.size >= VALUE_CACHE_SIZE) valueCache.delete(valueCache.keys().next().value!);
    valueCache.set(key, values);
  }
  return values;
}

function diffSummary(): DiffSummary | null {
  if (!state.a || !state.b) return null;
  const key = `${state.pair}:${state.compare.k}`;
  let summary = diffCache.get(key);
  if (!summary) {
    summary = summarizeDiff(state.a.image, state.b.image, state.pair, state.compare.k);
    diffCache.set(key, summary);
  }
  return summary;
}

function currentRange(): [number, number] {
  const { mode, low, high, manual, log } = state.range;
  const key = `${state.pair}:${state.stat}:${mode}:${low}:${high}:${manual.join(",")}:${log}`;
  let range = rangeCache.get(key);
  if (!range) {
    range = rangeOf(currentValues(), state.range);
    if (rangeCache.size >= 64) rangeCache.clear();
    rangeCache.set(key, range);
  }
  return range;
}

function diffRange(): number {
  return state.compare.range ?? diffSummary()?.autoRange[state.compare.view] ?? 1;
}

/** The automatic white of A, or 1 when it has no positive Y to use. */
function autoWhite(): number {
  const w = state.a?.image.stats.autoWhite ?? NaN;
  return Number.isFinite(w) && w > 0 ? w : 1;
}

const effectiveWhite = () => state.white ?? autoWhite();

const compareMode = (): CompareMode => (state.b ? state.compare.mode : "none");

function quantityOf(pair: Pair): Quantity | undefined {
  return state.a ? quantities(state.a.image.layout).find((q) => q.pair === pair) : undefined;
}

function settings(): RenderSettings {
  const mode = compareMode();
  return {
    mode: state.mode,
    exposure: state.exposure,
    white: effectiveWhite(),
    pair: state.pair,
    stat: state.stat,
    range: state.mode === "map" && mode !== "diff" ? currentRange() : [0, 0],
    log: state.range.log,
    compare: {
      mode,
      splitX: state.compare.split * canvas.width,
      pair: state.pair,
      k: state.compare.k,
      view: state.compare.view,
      range: mode === "diff" ? diffRange() : 1,
    },
  };
}

// --- Drawing --------------------------------------------------------------

let frame = 0;
function requestRender(): void {
  if (frame) return;
  frame = requestAnimationFrame(() => {
    frame = 0;
    draw();
  });
}

function draw(): void {
  const resized = renderer.resize();
  const image = state.a?.image;
  if (image && (resized || !state.view) && state.autoFit) {
    state.view = fitView(image.width, image.height, canvas.width, canvas.height);
  }
  const s = settings();
  renderer.render(state.view ?? { cx: 0, cy: 0, scale: 1 }, s);
  scheduleHashUpdate();
  zoomLabel.textContent = state.view ? `${formatZoom(state.view.scale / (window.devicePixelRatio || 1))}` : "";
  updateColorbar(s);
  updateCursor();
}

function formatZoom(cssScale: number): string {
  const pct = cssScale * 100;
  return `${pct >= 10 ? Math.round(pct) : pct.toPrecision(2)}%`;
}

function statisticLabel(q: Quantity, stat: Statistic): string {
  if (stat === "relative") return `${q.label} relative error`;
  const unit = q.unit ? ` (${q.unit})` : "";
  return `${q.label}${stat === "sigma" ? " σ" : ""}${unit}`;
}

function updateColorbar(s: RenderSettings): void {
  const q = quantityOf(state.pair);
  if (!q) {
    colorbar.hide();
    return;
  }
  if (s.compare.mode === "diff") {
    const { k, view, range } = s.compare;
    const unit = q.unit ? ` (${q.unit})` : "";
    const summary = diffSummary();
    const lines = [`Gray: |Δ| < ${formatNumber(k)} σΔ`];
    if (summary && summary.finite > 0) {
      lines.push(
        `${summary.significant} of ${summary.finite} pixels significant (${formatPercent(summary.significant / summary.finite)}; ` +
          `noise alone ≈ ${formatPercent(noiseOnlyFraction(k))})`,
      );
    }
    colorbar.show({
      title: view === "z" ? `z = Δ/σΔ of ${q.label}` : `Δ ${q.label} = A − B${unit}`,
      lut: state.compare.lut,
      range: [-range, range],
      log: false,
      note: lines.join("\n"),
    });
    return;
  }
  if (s.mode !== "map") {
    colorbar.hide();
    return;
  }
  colorbar.show({ title: `${statisticLabel(q, s.stat)}${s.log ? " · log" : ""}`, lut: state.lut, range: s.range, log: s.log });
}

function estimate(image: LoadedImage, x: number, y: number, q: Quantity, label = q.label): string {
  const mean = pixelValue(image, x, y, meanIndex(q.pair));
  const sigma = pixelValue(image, x, y, sigmaIndex(q.pair));
  const unit = q.unit ? ` ${q.unit}` : "";
  const rel = mean !== 0 ? ` (${formatPercent(sigma / Math.abs(mean))})` : "";
  return `${label} ${formatEstimate(mean, sigma)}${unit}${rel}`;
}

function readout(x: number, y: number): string {
  const a = state.a!.image;
  const all = quantities(a.layout);
  const mode = compareMode();
  if (mode !== "none") {
    // Compare one quantity: Y in image mode, otherwise the chosen pair.
    const pair: Pair = state.mode === "image" && mode === "split" ? 1 : state.pair;
    const q = all.find((p) => p.pair === pair)!;
    const b = state.b!.image;
    const d = diffAt(a, b, x, y, pair, state.compare.k);
    const verdict = d.significant ? "significant" : `within ${formatNumber(state.compare.k)}σ`;
    return [
      estimate(a, x, y, q, `A ${q.label}`),
      estimate(b, x, y, q, "B"),
      `Δ ${formatEstimate(d.delta, d.sigma)}`,
      `z ${Number.isFinite(d.z) ? d.z.toFixed(2) : d.z} (${verdict})`,
    ].join("  ·  ");
  }
  const shown = state.mode === "image" ? all.filter((q) => q.pair !== TIME_PAIR) : all.filter((q) => q.pair === state.pair);
  // Cost is always worth seeing next to the estimate it bought.
  const time = all.find((q) => q.pair === TIME_PAIR)!;
  if (state.pair !== TIME_PAIR || state.mode === "image") shown.push({ ...time, label: "cost" });
  return shown.map((q) => estimate(a, x, y, q)).join("  ·  ");
}

function updateCursor(): void {
  const { view, hover } = state;
  const image = state.a?.image;
  const pixel = image && view && hover ? pixelAt(view, canvas.width, canvas.height, image.width, image.height, ...hover) : null;
  cursorText.textContent = pixel ? `x ${pixel[0]}  y ${pixel[1]}  ·  ${readout(...pixel)}` : "";
}

// --- Image-mode controls --------------------------------------------------

const exposureControl = bindLogControl($("exposure-slider"), $("exposure"), {
  reference: () => 1,
  onChange(value) {
    state.exposure = value;
    syncControls();
  },
  onReset() {
    state.exposure = 1;
    syncControls();
  },
});

const whiteControl = bindLogControl($("white-slider"), $("white"), {
  reference: autoWhite,
  onChange(value) {
    state.white = value;
    syncControls();
  },
  onReset() {
    state.white = null;
    syncControls();
  },
});

whiteAutoButton.addEventListener("click", () => {
  state.white = null;
  syncControls();
});

modeSelect.addEventListener("change", () => {
  state.mode = modeSelect.value as DisplayMode;
  syncControls();
});

// --- Map-mode controls ----------------------------------------------------

for (const p of PALETTES) {
  paletteSelect.add(new Option(p.name, p.name));
  diffPaletteSelect.add(new Option(p.name, p.name));
}
for (const s of STATISTICS) statisticSelect.add(new Option(s.label, s.id));

function setPalette(name: string): void {
  state.palette = name;
  state.lut = bakeLut(paletteByName(name));
  renderer.setPalette(state.lut);
}
setPalette(DEFAULT_PALETTE);

function setDiffPalette(name: string): void {
  state.compare.palette = name;
  state.compare.lut = bakeLut(paletteByName(name));
  renderer.setDiffPalette(state.compare.lut);
}
setDiffPalette(DEFAULT_DIFF_PALETTE);

for (const select of [quantitySelect, diffQuantitySelect]) {
  select.addEventListener("change", () => {
    state.pair = Number(select.value) as Pair;
    syncControls();
  });
}
statisticSelect.addEventListener("change", () => {
  state.stat = statisticSelect.value as Statistic;
  syncControls();
});
paletteSelect.addEventListener("change", () => {
  setPalette(paletteSelect.value);
  syncControls();
});
rangeModeSelect.addEventListener("change", () => {
  const mode = rangeModeSelect.value as RangeMode;
  // Switching to manual starts from whatever range is showing.
  if (mode === "manual") state.range.manual = currentRange();
  state.range.mode = mode;
  syncControls();
});
logCheckbox.addEventListener("change", () => {
  state.range.log = logCheckbox.checked;
  syncControls();
});

/** Read a number field; null when blank, non-finite or rejected by `valid`. */
function readNumber(field: HTMLInputElement, valid: (v: number) => boolean = () => true): number | null {
  const v = Number(field.value);
  return field.value.trim() !== "" && Number.isFinite(v) && valid(v) ? v : null;
}

function onPercentChange(): void {
  const inRange = (v: number) => v >= 0 && v <= 100;
  const low = readNumber(pctLow, inRange);
  const high = readNumber(pctHigh, inRange);
  if (low === null || high === null) return;
  state.range.low = low;
  state.range.high = high;
  syncControls();
}
pctLow.addEventListener("change", onPercentChange);
pctHigh.addEventListener("change", onPercentChange);

function onRangeEdit(): void {
  const lo = readNumber(rangeMin);
  const hi = readNumber(rangeMax);
  if (lo === null || hi === null) return;
  state.range.mode = "manual";
  state.range.manual = [lo, hi];
  syncControls();
}
rangeMin.addEventListener("change", onRangeEdit);
rangeMax.addEventListener("change", onRangeEdit);

// --- Compare controls -----------------------------------------------------

compareSelect.addEventListener("change", () => {
  state.compare.mode = compareSelect.value as CompareMode;
  syncControls();
});
diffViewSelect.addEventListener("change", () => {
  state.compare.view = diffViewSelect.value as DiffView;
  // Δ and z live on different scales; a manual range doesn't carry over.
  state.compare.range = null;
  syncControls();
});
diffK.addEventListener("change", () => {
  const k = readNumber(diffK, (v) => v >= 0);
  if (k === null) return;
  state.compare.k = k;
  syncControls();
});
diffPaletteSelect.addEventListener("change", () => {
  setDiffPalette(diffPaletteSelect.value);
  syncControls();
});
diffRangeField.addEventListener("change", () => {
  const r = readNumber(diffRangeField, (v) => v > 0);
  if (r === null) return;
  state.compare.range = r;
  syncControls();
});
diffRangeAuto.addEventListener("click", () => {
  state.compare.range = null;
  syncControls();
});
swapButton.addEventListener("click", swapImages);

attachSplitHandle(splitHandle, stage, (fraction) => {
  state.compare.split = fraction;
  placeSplitHandle(splitHandle, fraction);
  requestRender();
});

// Restore a field's display value once it loses focus with a bad entry.
for (const field of [pctLow, pctHigh, rangeMin, rangeMax, diffK, diffRangeField]) {
  field.addEventListener("blur", () => syncControls());
}

/** Push state into the controls and redraw. */
function syncControls(): void {
  const loaded = state.a !== null;
  const mode = compareMode();
  const setField = (field: HTMLInputElement, value: string) => {
    if (document.activeElement !== field) field.value = value;
  };

  for (const slot of [0, 1] as const) {
    const s = slot === 0 ? state.a : state.b;
    const el = fileInfo[slot];
    if (s) {
      const { width, height, layout } = s.image;
      el.textContent = slot === 0 ? `A: ${s.name} · ${width}×${height} · ${LAYOUT_LABELS[layout]}` : `B: ${s.name}`;
      el.title = s.name;
      el.classList.remove("muted");
    }
    el.hidden = slot === 1 && !s;
  }
  openBButton.disabled = !loaded;
  compareSelect.disabled = !state.b;
  compareSelect.value = mode;
  swapButton.disabled = !state.b;
  fitButton.disabled = !loaded;
  actualButton.disabled = !loaded;
  exportMenu.hidden = !loaded;
  const gnuplotOk = mode === "diff" || (mode === "none" && state.mode === "map");
  gnuplotButton.disabled = !gnuplotOk;
  gnuplotButton.title = gnuplotOk ? "" : "Available in map mode and in difference mode";
  empty.hidden = loaded;

  controlsRow.hidden = !loaded;
  modeField.hidden = mode === "diff";
  modeSelect.value = state.mode;
  imageControls.hidden = state.mode !== "image" || mode === "diff";
  mapControls.hidden = state.mode !== "map" || mode === "diff";
  diffControls.hidden = mode !== "diff";
  splitHandle.hidden = mode !== "split";
  placeSplitHandle(splitHandle, state.compare.split);

  exposureControl.set(state.exposure);
  whiteControl.set(effectiveWhite());
  whiteAutoButton.setAttribute("aria-pressed", String(state.white === null));
  whiteAutoButton.title = `Use htpp's automatic white: the Y at 99.5 % of A's pixels (${formatValue(autoWhite())})`;

  quantitySelect.value = String(state.pair);
  diffQuantitySelect.value = String(state.pair);
  statisticSelect.value = state.stat;
  paletteSelect.value = state.palette;
  rangeModeSelect.value = state.range.mode;
  pctFields.hidden = state.range.mode !== "percentile";
  logCheckbox.checked = state.range.log;
  setField(pctLow, String(state.range.low));
  setField(pctHigh, String(state.range.high));
  if (loaded && state.mode === "map" && mode !== "diff") {
    const [lo, hi] = currentRange();
    setField(rangeMin, formatNumber(lo, 6));
    setField(rangeMax, formatNumber(hi, 6));
  }

  diffViewSelect.value = state.compare.view;
  setField(diffK, String(state.compare.k));
  diffPaletteSelect.value = state.compare.palette;
  diffRangeAuto.setAttribute("aria-pressed", String(state.compare.range === null));
  if (mode === "diff") setField(diffRangeField, formatNumber(diffRange(), 6));

  requestRender();
}

function populateQuantities(layout: Layout): void {
  for (const select of [quantitySelect, diffQuantitySelect]) {
    select.replaceChildren();
    for (const q of quantities(layout)) select.add(new Option(q.unit ? `${q.label} (${q.unit})` : q.label, String(q.pair)));
  }
  if (!quantities(layout).some((q) => q.pair === state.pair)) state.pair = DEFAULT_PAIR[layout];
}

// --- Loading --------------------------------------------------------------

function nonFiniteNote(image: LoadedImage, label: string): string {
  const bad = image.stats.values.slice(0, 6).reduce((n, v) => Math.max(n, v.nonFinite), 0);
  return bad > 0 ? `${label}: ${bad} pixels hold non-finite values (shown in magenta). ` : "";
}

function setImageA(image: LoadedImage, name: string, source?: string): void {
  const previousLayout = state.a?.image.layout;
  renderer.setImage(0, image);
  state.a = { image, name, source };
  clearCaches();
  state.view = null;
  state.autoFit = true;
  let note = nonFiniteNote(image, "A");
  if (state.b) {
    const problem = incompatibility(image, state.b.image);
    if (problem) {
      renderer.clearImage(1);
      state.b = null;
      note += `Closed B: ${problem}.`;
    }
  }
  // Image mode is only meaningful for CIE XYZ; htpp would read a
  // temperature as X. Each new image starts from the automatic white.
  const isXyz = image.layout === "xyz";
  if (!isXyz || previousLayout !== "xyz") state.mode = isXyz ? "image" : "map";
  state.white = null;
  if (image.layout !== previousLayout) state.pair = DEFAULT_PAIR[image.layout];
  modeSelect.options[0]!.disabled = !isXyz;
  modeSelect.title = isXyz ? "" : "Image mode needs a CIE XYZ image";
  populateQuantities(image.layout);
  setStatus(note.trim());
  // A link's mode, quantity, white and view wait for the first image.
  if (pendingUrl) {
    applyImageSettings(pendingUrl);
    pendingUrl = pendingUrl.compare && pendingUrl.compare !== "none" ? { compare: pendingUrl.compare } : null;
  }
  syncControls();
}

function setImageB(image: LoadedImage, name: string, source?: string): void {
  const problem = incompatibility(state.a!.image, image);
  if (problem) {
    setStatus(`Can't compare ${name} with A: ${problem}`, true);
    return;
  }
  renderer.setImage(1, image);
  state.b = { image, name, source };
  diffCache.clear();
  if (pendingUrl?.compare) {
    state.compare.mode = pendingUrl.compare;
    pendingUrl = null;
  } else if (state.compare.mode === "none") {
    state.compare.mode = "split";
  }
  setStatus(nonFiniteNote(image, "B").trim());
  syncControls();
}

function swapImages(): void {
  if (!state.a || !state.b) return;
  [state.a, state.b] = [state.b, state.a];
  renderer.swapImages();
  clearCaches();
  syncControls();
}

async function openFile(file: File, slot: 0 | 1, source?: string): Promise<void> {
  if (slot === 1 && !state.a) slot = 0;
  state.jobs[slot]?.cancel();
  const job = loadImageFile(file, (fraction) => (progress.value = fraction));
  state.jobs[slot] = job;
  progress.value = 0;
  progress.hidden = false;
  setStatus(`Loading ${file.name} as ${slot === 0 ? "A" : "B"}…`);
  const started = performance.now();
  try {
    const image = await job.promise;
    if (slot === 0) setImageA(image, file.name, source);
    else setImageB(image, file.name, source);
    if (!statusText.textContent) setStatus(`Loaded ${file.name} in ${((performance.now() - started) / 1000).toFixed(2)} s`);
  } catch (err) {
    if (err instanceof DOMException && err.name === "AbortError") return;
    const message = err instanceof Error ? err.message : String(err);
    setStatus(`Couldn't open ${file.name}: ${message}`, true);
    if (!(err instanceof LoadError)) console.error(err);
  } finally {
    if (state.jobs[slot] === job) {
      state.jobs[slot] = null;
      if (!state.jobs[0] && !state.jobs[1]) progress.hidden = true;
    }
  }
}

/** Fetch an htrdr-image from the viewer's own origin (a link's `a` or `b`) and open it. */
async function openSource(path: string, slot: 0 | 1): Promise<void> {
  setStatus(`Fetching ${path}…`);
  let response: Response;
  try {
    response = await fetch(path);
  } catch (err) {
    setStatus(`Couldn't fetch ${path}: ${err instanceof Error ? err.message : String(err)}`, true);
    return;
  }
  if (!response.ok) {
    setStatus(`Couldn't fetch ${path}: HTTP ${response.status}`, true);
    return;
  }
  // Dev servers answer unknown paths with the app's own page (status 200).
  if (response.headers.get("content-type")?.includes("text/html")) {
    setStatus(`Couldn't fetch ${path}: no such file (the server sent a web page)`, true);
    return;
  }
  const name = path.split("/").filter(Boolean).slice(-2).join("/");
  await openFile(new File([await response.blob()], name), slot, path);
}

/** Open a link's sources that aren't already open. */
async function openLinkedSources(u: UrlSettings): Promise<void> {
  if (u.a && u.a !== state.a?.source) await openSource(u.a, 0);
  if (u.b && state.a && u.b !== state.b?.source) await openSource(u.b, 1);
}

attachDropZone(
  $("drop-overlay"),
  () => state.a !== null,
  async (files, slot) => {
    // Two files at once: the first is A, the second B.
    if (files.length >= 2) {
      await openFile(files[0]!, 0);
      if (state.a) await openFile(files[1]!, 1);
    } else {
      await openFile(files[0]!, slot === "a" ? 0 : 1);
    }
  },
);
$("open").addEventListener("click", () => fileInputs[0].click());
openBButton.addEventListener("click", () => fileInputs[1].click());
fileInputs.forEach((input, slot) => {
  input.addEventListener("change", () => {
    const file = input.files?.[0];
    if (file) void openFile(file, slot as 0 | 1);
    input.value = "";
  });
});

// --- Export ---------------------------------------------------------------

type ExportKind = "png" | "ppm" | "gnuplot";

/** The on-screen settings, with the split moved to the image column under it. */
function exportSettings(): RenderSettings {
  const s = settings();
  if (s.compare.mode === "split" && state.view) {
    const [column] = canvasToImage(state.view, canvas.width, canvas.height, s.compare.splitX, 0);
    s.compare = { ...s.compare, splitX: column };
  }
  return s;
}

function exportName(ext: string): string {
  const a = stem(state.a!.name);
  const q = quantityOf(state.pair)?.label.replace(/\W+/g, "") ?? "";
  const mode = compareMode();
  if (mode === "diff") return `${a}-vs-${stem(state.b!.name)}-${state.compare.view === "z" ? "z" : "delta"}-${q}.${ext}`;
  if (mode === "split") return `${a}-vs-${stem(state.b!.name)}-split.${ext}`;
  if (state.mode === "image") return `${a}.${ext}`;
  return `${a}-${q}-${state.stat}.${ext}`;
}

/** Values and labels for a gnuplot export of what's on screen. */
function gnuplotSource(): { values: Float32Array; range: [number, number]; log: boolean; palette: string; title: string } {
  const a = state.a!;
  const q = quantityOf(state.pair)!;
  if (compareMode() === "diff") {
    const b = state.b!.image;
    const { k, view } = state.compare;
    const values = new Float32Array(a.image.width * a.image.height);
    for (let y = 0; y < a.image.height; y++) {
      for (let x = 0; x < a.image.width; x++) {
        const d = diffAt(a.image, b, x, y, state.pair, k);
        // Not significant: missing data, which gnuplot leaves blank.
        values[y * a.image.width + x] = d.significant ? (view === "z" ? d.z : d.delta) : NaN;
      }
    }
    const r = diffRange();
    const what = view === "z" ? `z = delta/sigma of ${q.label}` : `delta ${q.label} = A - B`;
    return {
      values,
      range: [-r, r],
      log: false,
      palette: state.compare.palette,
      title: `${what}, ${a.name} vs ${state.b!.name}; blank where |delta| < ${k} sigma`,
    };
  }
  return {
    values: derivedValues(a.image, state.pair, state.stat),
    range: currentRange(),
    log: state.range.log,
    palette: state.palette,
    title: `${statisticLabel(q, state.stat)} of ${a.name}`,
  };
}

async function exportAs(kind: ExportKind): Promise<void> {
  if (!state.a) return;
  const { width, height } = state.a.image;
  const name = exportName(kind === "gnuplot" ? "gp" : kind);
  setStatus(`Exporting ${name}…`);
  // Let the status paint before the synchronous work.
  await new Promise((r) => setTimeout(r, 0));
  try {
    let blob: Blob;
    if (kind === "gnuplot") {
      const src = gnuplotSource();
      blob = new Blob(gnuplotChunks({ width, height, ...src, palette: paletteByName(src.palette) }), { type: "text/plain" });
    } else {
      const rgba = renderer.renderToPixels(exportSettings());
      blob = kind === "png" ? await pngBlob(rgba, width, height) : new Blob(ppmChunks(rgba, width, height), { type: "image/x-portable-pixmap" });
    }
    downloadBlob(blob, name);
    setStatus(`Exported ${name} (${formatBytes(blob.size)})`);
  } catch (err) {
    setStatus(`Export failed: ${err instanceof Error ? err.message : String(err)}`, true);
  }
}

function formatBytes(n: number): string {
  if (n < 1024) return `${n} B`;
  if (n < 1024 ** 2) return `${(n / 1024).toFixed(1)} KB`;
  return `${(n / 1024 ** 2).toFixed(1)} MB`;
}

for (const button of exportMenu.querySelectorAll<HTMLButtonElement>("[data-export]")) {
  button.addEventListener("click", () => {
    exportMenu.open = false;
    void exportAs(button.dataset.export as ExportKind);
  });
}
document.addEventListener("pointerdown", (e) => {
  if (exportMenu.open && !exportMenu.contains(e.target as Node)) exportMenu.open = false;
});

// --- View -----------------------------------------------------------------

const fit = () => {
  state.autoFit = true;
  state.view = null;
  requestRender();
};
const actualSize = () => {
  if (!state.view) return;
  state.autoFit = false;
  state.view = { ...state.view, scale: window.devicePixelRatio || 1 };
  requestRender();
};
fitButton.addEventListener("click", fit);
actualButton.addEventListener("click", actualSize);

attachViewportControls(canvas, {
  getView: () => state.view,
  setView(view) {
    state.view = view;
    state.autoFit = false;
    requestRender();
  },
  fit,
  hover(px, py) {
    state.hover = [px, py];
    updateCursor();
  },
  leave() {
    state.hover = null;
    updateCursor();
  },
});

canvas.addEventListener("webglcontextlost", (e) => {
  e.preventDefault();
  setStatus("The GPU context was lost; reload the page to continue.", true);
});

// --- Keyboard -------------------------------------------------------------

const help = $("help");
renderHelp($("help-list"));
const toggleHelp = (show = help.hidden) => {
  help.hidden = !show;
};
$("help-button").addEventListener("click", () => toggleHelp());
help.addEventListener("click", (e) => {
  if (e.target === help) toggleHelp(false);
});
window.addEventListener("keydown", (e) => {
  if (e.key === "Escape" && !help.hidden) toggleHelp(false);
});

/** Back to defaults for the adjustments; what is shown (mode, quantity, compare) stays. */
function resetAdjustments(): void {
  state.exposure = 1;
  state.white = null;
  state.stat = "value";
  state.range = { ...DEFAULT_RANGE };
  setPalette(DEFAULT_PALETTE);
  state.compare.k = 2;
  state.compare.range = null;
  state.compare.split = 0.5;
  setDiffPalette(DEFAULT_DIFF_PALETTE);
  state.autoFit = true;
  state.view = null;
  syncControls();
}

const COMPARE_CYCLE: CompareMode[] = ["none", "split", "diff"];

attachShortcuts((action: ShortcutAction): boolean => {
  if (action === "help") {
    toggleHelp();
    return true;
  }
  if (!state.a) return false;
  const view = state.view;
  switch (action) {
    case "fit":
      fit();
      return true;
    case "actualSize":
      actualSize();
      return true;
    case "zoomIn":
    case "zoomOut":
      if (!view) return false;
      state.view = zoomAt(view, canvas.width, canvas.height, canvas.width / 2, canvas.height / 2, action === "zoomIn" ? 2 : 0.5);
      state.autoFit = false;
      requestRender();
      return true;
    case "swap":
      if (!state.b) return false;
      swapImages();
      return true;
    case "cycleCompare":
      if (!state.b) return false;
      state.compare.mode = COMPARE_CYCLE[(COMPARE_CYCLE.indexOf(compareMode()) + 1) % COMPARE_CYCLE.length]!;
      syncControls();
      return true;
    case "toggleMode":
      if (state.a.image.layout !== "xyz" || compareMode() === "diff") return false;
      state.mode = state.mode === "image" ? "map" : "image";
      syncControls();
      return true;
    case "reset":
      resetAdjustments();
      return true;
  }
});

// --- URL hash ---------------------------------------------------------------

/** Settings from the page's link that need an image (or B) before they apply. */
let pendingUrl: UrlSettings | null = null;

/** Apply the settings that make sense without an image. */
function applyGeneralSettings(u: UrlSettings): void {
  if (u.exposure !== undefined) state.exposure = u.exposure;
  if (u.stat) state.stat = u.stat;
  if (u.palette && PALETTES.some((p) => p.name === u.palette)) setPalette(u.palette);
  if (u.range) {
    state.range.mode = u.range.mode;
    if (u.range.low !== undefined) state.range.low = u.range.low;
    if (u.range.high !== undefined) state.range.high = u.range.high;
    if (u.range.manual) state.range.manual = u.range.manual;
  }
  if (u.log !== undefined) state.range.log = u.log;
  if (u.split !== undefined) state.compare.split = u.split;
  if (u.k !== undefined) state.compare.k = u.k;
  if (u.diffView) state.compare.view = u.diffView;
  if (u.diffPalette && PALETTES.some((p) => p.name === u.diffPalette)) setDiffPalette(u.diffPalette);
  if (u.diffRange !== undefined) state.compare.range = u.diffRange === "auto" ? null : u.diffRange;
}

/** Apply the settings that depend on the image: only those valid for it. */
function applyImageSettings(u: UrlSettings): void {
  const a = state.a;
  if (!a) return;
  if (u.mode && (u.mode === "map" || a.image.layout === "xyz")) state.mode = u.mode;
  if (u.pair !== undefined && quantities(a.image.layout).some((q) => q.pair === u.pair)) state.pair = u.pair;
  if (u.white !== undefined) state.white = u.white === "auto" ? null : u.white;
  if (u.compare && state.b) state.compare.mode = u.compare;
  if (u.view) {
    state.view = { cx: u.view.cx, cy: u.view.cy, scale: u.view.zoom * (window.devicePixelRatio || 1) };
    state.autoFit = false;
  }
}

function currentUrlSettings(): UrlSettings {
  // Sources only while the open images came from them: a dropped file has no URL.
  const u: UrlSettings = { a: state.a?.source, b: state.b?.source, mode: state.mode };
  if (state.mode === "image") {
    u.exposure = state.exposure;
    u.white = state.white ?? "auto";
  }
  if (state.mode === "map" || compareMode() === "diff") u.pair = state.pair;
  if (state.mode === "map") {
    u.stat = state.stat;
    u.palette = state.palette;
    const { mode, low, high, manual } = state.range;
    u.range = mode === "auto" ? { mode } : mode === "percentile" ? { mode, low, high } : { mode, manual: [manual[0], manual[1]] };
    if (state.range.log) u.log = true;
  }
  if (state.b) {
    u.compare = compareMode();
    if (u.compare === "split") u.split = state.compare.split;
    if (u.compare === "diff") {
      u.k = state.compare.k;
      u.diffView = state.compare.view;
      u.diffPalette = state.compare.palette;
      u.diffRange = state.compare.range ?? "auto";
    }
  }
  if (!state.autoFit && state.view) {
    u.view = { cx: state.view.cx, cy: state.view.cy, zoom: state.view.scale / (window.devicePixelRatio || 1) };
  }
  return u;
}

let hashTimer = 0;
/** Mirror the settings into the hash, quietly (no history entry, no hashchange). */
function scheduleHashUpdate(): void {
  // Before any image is open, keep the link the page was opened with.
  if (!state.a) return;
  clearTimeout(hashTimer);
  hashTimer = window.setTimeout(() => {
    const hash = `#${encodeSettings(currentUrlSettings())}`;
    if (hash !== location.hash) history.replaceState(null, "", hash);
  }, 200);
}

// A hand-edited hash applies at once.
window.addEventListener("hashchange", () => {
  const u = decodeSettings(location.hash);
  applyGeneralSettings(u);
  if ((u.a && u.a !== state.a?.source) || (u.b && u.b !== state.b?.source)) {
    // New images: their settings apply once they've loaded.
    pendingUrl = u;
    void openLinkedSources(u);
  } else {
    applyImageSettings(u);
  }
  syncControls();
});

{
  const u = decodeSettings(location.hash);
  applyGeneralSettings(u);
  if (Object.keys(u).length > 0) pendingUrl = u;
  void openLinkedSources(u);
}

new ResizeObserver(requestRender).observe(canvas);
syncControls();
