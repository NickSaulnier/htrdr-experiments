// Number formatting for the readout and colorbar.

const MAX_DECIMALS = 10;

/**
 * "mean ± σ" with both rounded to σ's second significant digit, the usual
 * way to quote a Monte Carlo estimate. Very large or small magnitudes share
 * one exponent: "(1.23 ± 0.05)e-7".
 */
export function formatEstimate(mean: number, sigma: number): string {
  if (!Number.isFinite(mean)) return String(mean);
  if (!(sigma > 0) || !Number.isFinite(sigma)) return formatNumber(mean);
  const last = Math.floor(Math.log10(sigma)) - 1; // decimal exponent of the last kept digit
  const magnitude = Math.floor(Math.log10(Math.max(Math.abs(mean), sigma)));
  if (magnitude >= -3 && magnitude < 6) {
    const d = Math.min(Math.max(0, -last), MAX_DECIMALS);
    return `${mean.toFixed(d)} ± ${sigma.toFixed(d)}`;
  }
  const scale = 10 ** magnitude;
  const d = Math.min(Math.max(0, magnitude - last), MAX_DECIMALS);
  return `(${(mean / scale).toFixed(d)} ± ${(sigma / scale).toFixed(d)})e${magnitude}`;
}

/** Relative error as a percentage with two significant digits. */
export function formatPercent(fraction: number): string {
  if (!Number.isFinite(fraction)) return "—";
  return `${Number((fraction * 100).toPrecision(2))} %`;
}

/** A plain value with up to 4 significant digits, for ticks and fields. */
export function formatNumber(v: number, digits = 4): string {
  if (!Number.isFinite(v)) return String(v);
  if (v === 0) return "0";
  const a = Math.abs(v);
  if (a >= 1e-3 && a < 1e6) return String(Number(v.toPrecision(digits)));
  return v.toExponential(digits - 1).replace(/\.?0+e/, "e");
}
