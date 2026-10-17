// Write a large synthetic htrdr-image for performance work (git-ignored).
//
//   pnpm tsx tools/gen-large.ts [width] [height]   # default 3840 2160

import { createWriteStream, mkdirSync } from "node:fs";
import { join } from "node:path";

const width = Number(process.argv[2] ?? 3840);
const height = Number(process.argv[3] ?? 2160);
const dir = join(import.meta.dirname, "..", "fixtures", "large");
mkdirSync(dir, { recursive: true });
const path = join(dir, `xyz-${width}x${height}.txt`);
const out = createWriteStream(path);

let seed = 7;
const rand = () => ((seed = (seed * 1103515245 + 12345) >>> 0) / 4294967296);

out.write(`${width} ${height}\n`);
for (let y = 0; y < height; y++) {
  let row = "";
  for (let x = 0; x < width; x++) {
    const u = x / width;
    const v = y / height;
    const L = 2 + 18 * (1 - v) + (Math.hypot(u - 0.7, v - 0.3) < 0.12 ? 120 : 0);
    const n = (s: number) => (1 + s * (rand() - 0.5)).toPrecision(6);
    row += `${(L * 0.9 * +n(0.1)).toPrecision(6)} ${(L * 0.03).toPrecision(6)} ${(L * +n(0.1)).toPrecision(6)} ${(L * 0.03).toPrecision(6)} ${(L * 1.2 * +n(0.1)).toPrecision(6)} ${(L * 0.04).toPrecision(6)} ${(200 + 100 * rand()).toPrecision(6)} 31.25\n`;
  }
  if (!out.write(row)) await new Promise((r) => out.once("drain", r));
}
out.end(() => console.log(`wrote ${path}`));
