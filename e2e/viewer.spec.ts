import { readFileSync } from "node:fs";
import { join } from "node:path";
import { expect, test, type Page } from "@playwright/test";
import { GRAY } from "../src/color/palettes";
import { parsePPM } from "../src/export/formats";
import { loadFromBytes } from "../src/format/loadedImage";
import { comparePixels, expectedPixels } from "../src/render/parity";
import { NO_COMPARE } from "../src/render/settings";

const fixture = (name: string) => join(import.meta.dirname, "..", "fixtures", name);

/** Fail the test on any uncaught page error or console error. */
function watchErrors(page: Page): string[] {
  const errors: string[] = [];
  page.on("pageerror", (e) => errors.push(`pageerror: ${e.message}`));
  page.on("console", (m) => {
    if (m.type() === "error") errors.push(`console: ${m.text()}`);
  });
  return errors;
}

async function openA(page: Page, name: string) {
  await page.setInputFiles("#file-input", fixture(name));
  await expect(page.locator("#file-info")).toContainText(name);
}

async function openB(page: Page, name: string) {
  await page.setInputFiles("#file-input-b", fixture(name));
  await expect(page.locator("#file-info-b")).toContainText(name);
}

/**
 * Record what the page hands the browser to download: the Blob given to
 * URL.createObjectURL and the anchor's file name. Call after page.goto.
 * Reading it from the page works the same in every engine (Playwright's
 * download events aren't available yet when it drives Firefox over BiDi).
 */
async function captureDownloads(page: Page) {
  // A <script> tag, not addInitScript: over BiDi, Firefox runs init scripts
  // in a sandboxed realm whose objects the page may not touch.
  await page.addScriptTag({
    content: `
      window.__exports = [];
      let lastBlob = null;
      const create = URL.createObjectURL.bind(URL);
      URL.createObjectURL = (obj) => {
        if (obj instanceof Blob) lastBlob = obj;
        return create(obj);
      };
      HTMLAnchorElement.prototype.click = function () {
        if (this.download) window.__exports.push({ name: this.download, blob: lastBlob });
      };
    `,
  });
}

async function download(page: Page, kind: "png" | "ppm" | "gnuplot") {
  const count = await page.evaluate(() => (window as unknown as { __exports: unknown[] }).__exports.length);
  await page.locator("#export-menu summary").click();
  await page.locator(`[data-export="${kind}"]`).click();
  await expect.poll(() => page.evaluate(() => (window as unknown as { __exports: unknown[] }).__exports.length)).toBe(count + 1);
  const { name, base64 } = await page.evaluate(async () => {
    const e = (window as unknown as { __exports: { name: string; blob: Blob }[] }).__exports.at(-1)!;
    const bytes = new Uint8Array(await e.blob.arrayBuffer());
    let s = "";
    for (let i = 0; i < bytes.length; i += 0x8000) s += String.fromCharCode(...bytes.subarray(i, i + 0x8000));
    return { name: e.name, base64: btoa(s) };
  });
  return { name, bytes: Buffer.from(base64, "base64") };
}

test("the GPU matches the CPU reference of htpp", async ({ page }) => {
  await page.goto("/parity.html");
  const result = await page.waitForFunction(() => (window as unknown as { parityResult?: unknown }).parityResult, null, {
    timeout: 110_000,
  });
  const r = (await result.jsonValue()) as { total: number; failures: number; maxDiff: number; error?: string };
  expect(r.error).toBeUndefined();
  expect(r.total).toBe(256);
  expect(r.failures, `failing rows: ${await page.locator("#results tr:has(.fail)").allInnerTexts()}`).toBe(0);
});

test("opens a file, renders it and exports what htpp would", async ({ page }) => {
  const errors = watchErrors(page);
  await page.goto("/");
  await captureDownloads(page);
  await openA(page, "xyz.txt");
  await expect(page.locator("#file-info")).toContainText("64×48 · CIE XYZ");
  await expect(page.locator("#mode")).toHaveValue("image");

  // The PPM export goes through the whole GPU path; check it against the CPU
  // reference, which tools/htpp-parity.ts checks against real htpp.
  const ppm = await download(page, "ppm");
  expect(ppm.name).toBe("xyz.ppm");
  const got = parsePPM(ppm.bytes.toString("utf8"));
  const img = loadFromBytes(readFileSync(fixture("xyz.txt")));
  const settings = { mode: "image" as const, exposure: 1, white: img.stats.autoWhite, pair: 1 as const, stat: "value" as const, range: [0, 0] as const, log: false, compare: NO_COMPARE };
  const cmp = comparePixels(expectedPixels(img, settings, { palette: GRAY }), got.rgba);
  expect(cmp.mismatches).toBe(0);

  const png = await download(page, "png");
  expect(png.bytes.subarray(0, 8).toString("hex")).toBe("89504e470d0a1a0a");
  expect(png.bytes.readUInt32BE(16)).toBe(64); // IHDR width
  expect(png.bytes.readUInt32BE(20)).toBe(48); // IHDR height
  expect(errors).toEqual([]);
});

test("map mode: switcher, colorbar and the value ± σ readout", async ({ page }) => {
  const errors = watchErrors(page);
  await page.goto("/");
  await openA(page, "longwave.txt");
  await expect(page.locator("#mode")).toHaveValue("map");
  // toBeDisabled doesn't apply to <option>; check the attribute.
  await expect(page.locator("#mode option[value=image]")).toHaveAttribute("disabled", "");
  await expect(page.locator(".colorbar-title")).toHaveText("Brightness temperature (K)");

  await page.locator("#statistic").selectOption("relative");
  await expect(page.locator(".colorbar-title")).toHaveText("Brightness temperature relative error");
  await page.locator("#range-mode").selectOption("percentile");
  await expect(page.locator("#pct-fields")).toBeVisible();

  const box = (await page.locator("#view").boundingBox())!;
  await page.mouse.move(box.x + box.width / 2, box.y + box.height / 2);
  await expect(page.locator("#cursor")).toContainText(/Brightness temperature \d+(\.\d+)? ± \d+(\.\d+)? K/);
  expect(errors).toEqual([]);
});

test("A/B: split, difference, swap and help from the keyboard", async ({ page }) => {
  const errors = watchErrors(page);
  await page.goto("/");
  await openA(page, "ab-a.txt");
  await openB(page, "ab-b.txt");
  await expect(page.locator("#compare")).toHaveValue("split");
  await expect(page.locator("#split")).toBeVisible();

  await page.locator("body").press("c");
  await expect(page.locator("#compare")).toHaveValue("diff");
  await expect(page.locator(".colorbar-note")).toContainText(/\d+ of 3072 pixels significant/);
  await expect(page.locator(".colorbar-title")).toHaveText("Δ Y = A − B (W/sr/m²)");

  await page.locator("body").press("s");
  await expect(page.locator("#file-info")).toContainText("ab-b.txt");
  await expect(page.locator("#file-info-b")).toContainText("ab-a.txt");

  await page.locator("body").press("?");
  await expect(page.locator("#help")).toBeVisible();
  await expect(page.locator("#help-list")).toContainText("Swap A and B");
  await page.locator("body").press("Escape");
  await expect(page.locator("#help")).toBeHidden();

  // An incompatible B is refused and the current one kept.
  await page.setInputFiles("#file-input-b", fixture("longwave.txt"));
  await expect(page.locator("#status")).toContainText("B is a longwave image but A is xyz");
  await expect(page.locator("#file-info-b")).toContainText("ab-a.txt");
  expect(errors).toEqual([]);
});

test("settings come from and go to the URL hash", async ({ page }) => {
  const errors = watchErrors(page);
  await page.goto("/#m=map&q=1&s=sigma&pal=viridis&r=pct:2:98&log=1");
  await openA(page, "xyz.txt");
  await expect(page.locator("#mode")).toHaveValue("map");
  await expect(page.locator("#statistic")).toHaveValue("sigma");
  await expect(page.locator("#palette")).toHaveValue("viridis");
  await expect(page.locator("#range-mode")).toHaveValue("percentile");
  await expect(page.locator("#pct-low")).toHaveValue("2");
  await expect(page.locator("#log")).toBeChecked();

  await page.locator("#palette").selectOption("magma");
  await expect.poll(() => page.evaluate(() => location.hash)).toContain("pal=magma");

  // Reloading the link and dropping the same file restores the view.
  await page.reload();
  await openA(page, "xyz.txt");
  await expect(page.locator("#palette")).toHaveValue("magma");
  await expect(page.locator("#statistic")).toHaveValue("sigma");

  // R resets the adjustments but keeps what's shown.
  await page.locator("body").press("r");
  await expect(page.locator("#statistic")).toHaveValue("value");
  await expect(page.locator("#palette")).toHaveValue("inferno");
  await expect(page.locator("#mode")).toHaveValue("map");
  expect(errors).toEqual([]);
});

test("a malformed file is reported with its line", async ({ page }) => {
  await page.goto("/");
  await page.setInputFiles("#file-input", { name: "broken.txt", mimeType: "text/plain", buffer: Buffer.from("2 1\n1 2 3 4 5 6 7 8\n1 2 x 4 5 6 7 8\n") });
  await expect(page.locator("#status")).toHaveText('Couldn\'t open broken.txt: line 3: invalid number "x"');
  await expect(page.locator("#status")).toHaveClass(/error/);
});
