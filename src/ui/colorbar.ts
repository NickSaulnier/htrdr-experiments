// The legend: palette ramp with low, middle and high values.

import { formatNumber } from "./format";

export interface ColorbarContent {
  title: string;
  lut: Uint8Array;
  range: readonly [number, number];
  log: boolean;
  /** Explains the gray swatch in difference mode. */
  note?: string;
}

export class Colorbar {
  private readonly title: HTMLElement;
  private readonly ramp: HTMLCanvasElement;
  private readonly ticks: [HTMLElement, HTMLElement, HTMLElement];
  private readonly note: HTMLElement;
  private lut: Uint8Array | null = null;

  constructor(private readonly root: HTMLElement) {
    this.title = root.querySelector(".colorbar-title")!;
    this.ramp = root.querySelector("canvas")!;
    this.ticks = [...root.querySelectorAll<HTMLElement>(".colorbar-ticks span")] as [HTMLElement, HTMLElement, HTMLElement];
    this.note = root.querySelector(".colorbar-note")!;
  }

  hide(): void {
    this.root.hidden = true;
  }

  show({ title, lut, range, log, note }: ColorbarContent): void {
    this.root.hidden = false;
    this.title.textContent = title;
    if (lut !== this.lut) {
      this.lut = lut;
      this.ramp.width = lut.length / 4;
      const ctx = this.ramp.getContext("2d")!;
      ctx.putImageData(new ImageData(new Uint8ClampedArray(lut), lut.length / 4, 1), 0, 0);
    }
    const [lo, hi] = range;
    const valid = log ? lo > 0 && hi > 0 : lo !== hi;
    const mid = log ? Math.sqrt(lo * hi) : (lo + hi) / 2;
    const labels = valid ? [lo, mid, hi].map((v) => formatNumber(v)) : ["—", "empty range", "—"];
    labels.forEach((text, i) => (this.ticks[i]!.textContent = text));
    this.note.hidden = !note;
    this.note.textContent = note ?? "";
  }
}
