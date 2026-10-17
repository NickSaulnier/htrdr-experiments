#version 300 es
// Draws the image through the pan/zoom transform.
//   Image mode: htpp's pp_image (filmic tone map, XYZ -> sRGB, sRGB OETF).
//   Map mode:   one of the 8 values per pixel through a palette (pp_map).
// Comparing two images A and B:
//   Split:      A left of uSplitX, B right of it, each drawn as above.
//   Difference: Δ = A - B of one pair, gray where |Δ| < k·σΔ.
// src/render/parity.ts is the CPU reference for all of it.
precision highp float;
precision highp int;
precision highp sampler2D;

uniform sampler2D uPlane0;  // A, values 0-3
uniform sampler2D uPlane1;  // A, values 4-7
uniform sampler2D uPlaneB0; // B, values 0-3
uniform sampler2D uPlaneB1; // B, values 4-7
uniform sampler2D uLut;     // LUT_SIZE x 1 RGBA8 palette (the diverging one in diff mode)

uniform vec2 uCanvasSize;  // device pixels
uniform vec2 uImageSize;   // image pixels
uniform vec2 uCenter;      // image point at the canvas centre
uniform float uScale;      // device pixels per image pixel

uniform int uMode;         // 0 = map, 1 = image

uniform float uExposure;
uniform float uWhiteScale; // filmic(white * exposure), computed on the CPU

uniform int uPair;         // (mean, sigma) pair 0-3
uniform int uStat;         // 0 = mean, 1 = sigma, 2 = sigma / |mean|
uniform bool uLog;         // map log10 of the value
uniform vec2 uRange;       // palette ends, already log10'd when uLog

uniform int uCompare;      // 0 = A only, 1 = split, 2 = difference
uniform float uSplitX;     // split: first device-pixel column showing B
uniform int uDiffPair;     // difference: pair compared
uniform float uK;          // difference: significance threshold in σΔ
uniform bool uDiffZ;       // difference: show z = Δ/σΔ instead of Δ
uniform float uDiffRange;  // difference: half-width of the symmetric range

out vec4 outColor;

const vec3 BACKGROUND = vec3(0.067, 0.075, 0.086);
const vec3 NON_FINITE = vec3(1.0, 0.0, 1.0);
const vec3 INSIGNIFICANT = vec3(0.3); // matches parity.ts
const float LUT_SIZE = 1024.0; // matches LUT_SIZE in palettes.ts

// htpp multiplies XYZ by the D65 white vector before the matrix.
const vec3 D65 = vec3(0.31271 / 0.32902, 1.0, (1.0 - 0.31271 - 0.32902) / 0.32902);
// Column-major, which is also how htpp lists it.
const mat3 XYZ_TO_SRGB = mat3(
   3.2404542, -0.9692660,  0.0556434,
  -1.5371385,  1.8760108, -0.2040259,
  -0.4985314,  0.0415560,  1.0572252);

// Bit test instead of isnan/isinf, which some drivers optimise away.
bool isFinite(float v) {
  return (floatBitsToUint(v) & 0x7f800000u) != 0x7f800000u;
}

vec2 fetchPair(sampler2D plane0, sampler2D plane1, ivec2 p, int pair) {
  vec4 texel = pair < 2 ? texelFetch(plane0, p, 0) : texelFetch(plane1, p, 0);
  return (pair & 1) == 0 ? texel.xy : texel.zw;
}

float fetchValue(sampler2D plane0, sampler2D plane1, ivec2 p) {
  vec2 ms = fetchPair(plane0, plane1, p, uPair);
  if (uStat == 0) return ms.x;
  if (uStat == 1) return ms.y;
  return ms.y / abs(ms.x);
}

vec3 lut(float t) {
  return texture(uLut, vec2((t * (LUT_SIZE - 1.0) + 0.5) / LUT_SIZE, 0.5)).rgb;
}

vec3 mapColor(float v) {
  float size = uRange.y - uRange.x;
  if (size == 0.0) return vec3(0.0);
  float t;
  if (uLog) {
    // log2(v) is undefined for v <= 0; those land on the low end.
    t = v > 0.0 ? clamp((log2(v) * 0.30102999566 - uRange.x) / size, 0.0, 1.0) : 0.0;
  } else {
    t = clamp((v - uRange.x) / size, 0.0, 1.0);
  }
  return lut(t);
}

// htpp's filmic curve rearranged so T(x) carries no "- E/F" cancellation,
// which costs float32 precision in the shadows (see filmicStable).
vec3 filmic(vec3 x) {
  const float A = 0.15, B = 0.50, C = 0.10, D = 0.20, E = 0.02, F = 0.30;
  return x * ((F - E) * A * x + (F * C - E) * B) / (F * (x * (A * x + B) + D * F));
}

vec3 srgbEncode(vec3 c) {
  return mix(1.055 * pow(c, vec3(1.0 / 2.4)) - 0.055, c * 12.92, lessThanEqual(c, vec3(0.0031308)));
}

vec3 toneMap(vec3 xyz) {
  if (uWhiteScale <= 0.0) return vec3(0.0);
  vec3 mapped = filmic(xyz * uExposure) / uWhiteScale * D65;
  return srgbEncode(max(XYZ_TO_SRGB * mapped, 0.0));
}

/** One image in image or map mode. */
vec3 shade(sampler2D plane0, sampler2D plane1, ivec2 p) {
  if (uMode == 1) {
    vec3 xyz = vec3(texelFetch(plane0, p, 0).xz, texelFetch(plane1, p, 0).x);
    bool finite = isFinite(xyz.x) && isFinite(xyz.y) && isFinite(xyz.z);
    return finite ? toneMap(xyz) : NON_FINITE;
  }
  float v = fetchValue(plane0, plane1, p);
  return isFinite(v) ? mapColor(v) : NON_FINITE;
}

vec3 shadeDifference(ivec2 p) {
  vec2 a = fetchPair(uPlane0, uPlane1, p, uDiffPair);
  vec2 b = fetchPair(uPlaneB0, uPlaneB1, p, uDiffPair);
  float delta = a.x - b.x;
  float sigma = sqrt(a.y * a.y + b.y * b.y);
  if (!isFinite(delta) || !isFinite(sigma)) return NON_FINITE;
  if (delta == 0.0 || abs(delta) < uK * sigma) return INSIGNIFICANT;
  if (uDiffRange <= 0.0) return vec3(0.0);
  // An exact difference (sigma = 0) has z = ±inf, which clamps to an end.
  float v = uDiffZ ? delta / sigma : delta;
  return lut(clamp((v + uDiffRange) / (2.0 * uDiffRange), 0.0, 1.0));
}

void main() {
  vec2 frag = vec2(gl_FragCoord.x, uCanvasSize.y - gl_FragCoord.y);
  vec2 img = (frag - 0.5 * uCanvasSize) / uScale + uCenter;
  if (any(lessThan(img, vec2(0.0))) || any(greaterThanEqual(img, uImageSize))) {
    outColor = vec4(BACKGROUND, 1.0);
    return;
  }
  ivec2 p = ivec2(floor(img));
  vec3 color;
  if (uCompare == 2) color = shadeDifference(p);
  else if (uCompare == 1 && frag.x >= uSplitX) color = shade(uPlaneB0, uPlaneB1, p);
  else color = shade(uPlane0, uPlane1, p);
  outColor = vec4(color, 1.0);
}
