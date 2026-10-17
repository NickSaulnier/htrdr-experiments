import { filmic } from "../color/tonemap";
import { STATISTIC_CODE } from "../format/derived";
import type { LoadedImage } from "../format/loadedImage";
import { createFloatTexture, createLutTexture, createProgram, uniformLocations } from "./gl";
import vertexSource from "./shaders/fullscreen.vert?raw";
import fragmentSource from "./shaders/view.frag?raw";
import { NO_COMPARE, shaderRange, type CompareMode, type RenderSettings } from "./settings";
import type { View } from "./view";

export type { CompareMode, CompareSettings, DisplayMode, RenderSettings } from "./settings";
export { NO_COMPARE, shaderRange } from "./settings";

const UNIFORMS = [
  "uPlane0",
  "uPlane1",
  "uPlaneB0",
  "uPlaneB1",
  "uLut",
  "uCanvasSize",
  "uImageSize",
  "uCenter",
  "uScale",
  "uMode",
  "uExposure",
  "uWhiteScale",
  "uPair",
  "uStat",
  "uLog",
  "uRange",
  "uCompare",
  "uSplitX",
  "uDiffPair",
  "uK",
  "uDiffZ",
  "uDiffRange",
] as const;

const COMPARE_CODE: Record<CompareMode, number> = { none: 0, split: 1, diff: 2 };

type Planes = [WebGLTexture, WebGLTexture];

export class Renderer {
  readonly gl: WebGL2RenderingContext;
  readonly maxTextureSize: number;
  private readonly program: WebGLProgram;
  private readonly uniforms: Record<(typeof UNIFORMS)[number], WebGLUniformLocation | null>;
  private readonly vao: WebGLVertexArrayObject;
  /** Slot 0 is A, slot 1 is B. */
  private slots: [Planes | null, Planes | null] = [null, null];
  private imageSize: [number, number] = [0, 0];
  private lut: WebGLTexture | null = null;
  private diffLut: WebGLTexture | null = null;

  constructor(readonly canvas: HTMLCanvasElement) {
    const gl = canvas.getContext("webgl2", { antialias: false, alpha: false, preserveDrawingBuffer: false });
    if (!gl) throw new Error("This viewer needs WebGL2, which this browser doesn't provide.");
    this.gl = gl;
    this.maxTextureSize = gl.getParameter(gl.MAX_TEXTURE_SIZE) as number;
    this.program = createProgram(gl, vertexSource, fragmentSource);
    this.uniforms = uniformLocations(gl, this.program, UNIFORMS);
    this.vao = gl.createVertexArray();
  }

  /** Upload an image into slot A (0) or B (1). The caller keeps B the same size as A. */
  setImage(slot: 0 | 1, img: LoadedImage): void {
    const { gl } = this;
    if (img.width > this.maxTextureSize || img.height > this.maxTextureSize) {
      throw new Error(`${img.width}x${img.height} exceeds this GPU's texture limit of ${this.maxTextureSize}`);
    }
    this.clearImage(slot);
    this.slots[slot] = [
      createFloatTexture(gl, img.width, img.height, img.planes[0]),
      createFloatTexture(gl, img.width, img.height, img.planes[1]),
    ];
    if (slot === 0) this.imageSize = [img.width, img.height];
  }

  clearImage(slot: 0 | 1): void {
    this.slots[slot]?.forEach((t) => this.gl.deleteTexture(t));
    this.slots[slot] = null;
  }

  swapImages(): void {
    this.slots = [this.slots[1], this.slots[0]];
  }

  setPalette(lut: Uint8Array): void {
    if (this.lut) this.gl.deleteTexture(this.lut);
    this.lut = createLutTexture(this.gl, lut);
  }

  setDiffPalette(lut: Uint8Array): void {
    if (this.diffLut) this.gl.deleteTexture(this.diffLut);
    this.diffLut = createLutTexture(this.gl, lut);
  }

  /** Match the drawing buffer to the canvas's CSS size. Returns true if it changed. */
  resize(): boolean {
    const dpr = window.devicePixelRatio || 1;
    const w = Math.max(1, Math.round(this.canvas.clientWidth * dpr));
    const h = Math.max(1, Math.round(this.canvas.clientHeight * dpr));
    if (this.canvas.width === w && this.canvas.height === h) return false;
    this.canvas.width = w;
    this.canvas.height = h;
    return true;
  }

  render(view: View, settings: RenderSettings): void {
    this.draw(this.canvas.width, this.canvas.height, view, settings);
  }

  /**
   * Render the whole image at 1:1 into an RGBA8 framebuffer and read it back,
   * rows top-down like the file. Used for exports and the parity check; a
   * split position is then in image columns.
   */
  renderToPixels(settings: RenderSettings): Uint8Array {
    const { gl } = this;
    const [w, h] = this.imageSize;
    if (!this.slots[0]) throw new Error("no image loaded");
    const target = gl.createTexture();
    gl.bindTexture(gl.TEXTURE_2D, target);
    gl.texStorage2D(gl.TEXTURE_2D, 1, gl.RGBA8, w, h);
    const fbo = gl.createFramebuffer();
    gl.bindFramebuffer(gl.FRAMEBUFFER, fbo);
    gl.framebufferTexture2D(gl.FRAMEBUFFER, gl.COLOR_ATTACHMENT0, gl.TEXTURE_2D, target, 0);
    try {
      this.draw(w, h, { cx: w / 2, cy: h / 2, scale: 1 }, settings);
      const raw = new Uint8Array(w * h * 4);
      gl.readPixels(0, 0, w, h, gl.RGBA, gl.UNSIGNED_BYTE, raw);
      // Framebuffer row 0 is the bottom of the image.
      const out = new Uint8Array(raw.length);
      const stride = w * 4;
      for (let y = 0; y < h; y++) out.set(raw.subarray((h - 1 - y) * stride, (h - y) * stride), y * stride);
      return out;
    } finally {
      gl.bindFramebuffer(gl.FRAMEBUFFER, null);
      gl.deleteFramebuffer(fbo);
      gl.deleteTexture(target);
    }
  }

  private draw(width: number, height: number, view: View, s: RenderSettings): void {
    const { gl, uniforms: u } = this;
    gl.viewport(0, 0, width, height);
    const [a, b] = this.slots;
    if (!a || !this.lut) {
      gl.clearColor(0.067, 0.075, 0.086, 1);
      gl.clear(gl.COLOR_BUFFER_BIT);
      return;
    }
    const compare = b ? s.compare : NO_COMPARE;
    const lut = compare.mode === "diff" ? (this.diffLut ?? this.lut) : this.lut;
    gl.useProgram(this.program);
    gl.bindVertexArray(this.vao);
    // Without B its samplers still need a texture bound; A stands in.
    const textures = [a[0], a[1], (b ?? a)[0], (b ?? a)[1], lut];
    textures.forEach((tex, unit) => {
      gl.activeTexture(gl.TEXTURE0 + unit);
      gl.bindTexture(gl.TEXTURE_2D, tex);
    });
    gl.uniform1i(u.uPlane0, 0);
    gl.uniform1i(u.uPlane1, 1);
    gl.uniform1i(u.uPlaneB0, 2);
    gl.uniform1i(u.uPlaneB1, 3);
    gl.uniform1i(u.uLut, 4);
    gl.uniform2f(u.uCanvasSize, width, height);
    gl.uniform2f(u.uImageSize, this.imageSize[0], this.imageSize[1]);
    gl.uniform2f(u.uCenter, view.cx, view.cy);
    gl.uniform1f(u.uScale, view.scale);
    gl.uniform1i(u.uMode, s.mode === "image" ? 1 : 0);
    gl.uniform1f(u.uExposure, s.exposure);
    // In double precision, exactly as htpp computes white_scale.
    gl.uniform1f(u.uWhiteScale, filmic(s.white * s.exposure));
    gl.uniform1i(u.uPair, s.pair);
    gl.uniform1i(u.uStat, STATISTIC_CODE[s.stat]);
    gl.uniform1i(u.uLog, s.log ? 1 : 0);
    const [lo, hi] = shaderRange(s.range, s.log);
    gl.uniform2f(u.uRange, lo, hi);
    gl.uniform1i(u.uCompare, COMPARE_CODE[compare.mode]);
    gl.uniform1f(u.uSplitX, compare.splitX);
    gl.uniform1i(u.uDiffPair, compare.pair);
    gl.uniform1f(u.uK, compare.k);
    gl.uniform1i(u.uDiffZ, compare.view === "z" ? 1 : 0);
    gl.uniform1f(u.uDiffRange, compare.range);
    gl.drawArrays(gl.TRIANGLES, 0, 3);
  }
}
