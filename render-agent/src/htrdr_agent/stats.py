"""Summaries of a rendered htrdr-image: what read_stats returns.

Never pixels: the agent decides from the distribution of the per-pixel
relative error, the cost of the render, and where the error concentrates.
Everything here is plain data (dataclasses of floats and lists) so it
serializes straight into MCP tool results and run logs.
"""

from __future__ import annotations

import math
from dataclasses import asdict, dataclass, field

import numpy as np

from .image import HtrdrImage, Layout, brightness_temperature, brightness_temperature_sigma
from .scene import Spectral

PERCENTILES = (50, 90, 95, 99)
TIME_PAIR = 3

# The quantity a target uncertainty applies to: Y for CIE XYZ, the radiance
# otherwise. Brightness temperature is not used because its relative error
# understates the radiance's (T varies like log L).
_QUANTITIES: dict[Layout, list[tuple[int, str, str]]] = {
    "xyz": [(0, "X", "W/sr/m²"), (1, "Y", "W/sr/m²"), (2, "Z", "W/sr/m²")],
    "longwave": [(0, "brightness temperature", "K"), (1, "radiance", "W/sr/m²")],
    "shortwave": [(1, "radiance", "W/sr/m²")],
}
_TARGET_PAIR = 1


@dataclass(frozen=True)
class QuantityStats:
    name: str
    unit: str
    mean: float
    min: float
    max: float


@dataclass(frozen=True)
class ErrorStats:
    quantity: str
    """Relative error σ/|μ| over pixels with a finite, non-zero mean."""
    percentiles: dict[str, float]
    max: float
    pixels: int
    target: float | None = None
    fraction_meeting_target: float | None = None
    worst_tiles: list[dict[str, float | int]] = field(default_factory=list)
    """The tiles with the highest p95 relative error, worst first."""


@dataclass(frozen=True)
class CostStats:
    mean_time_per_path_us: float
    paths: int
    """spp × pixels, ×3 for CIE XYZ (one set of paths per component)."""
    cpu_s: float
    """Σ over pixels of time per path × paths per pixel."""


@dataclass(frozen=True)
class ImageStats:
    width: int
    height: int
    layout: Layout
    spp: int
    quantities: list[QuantityStats]
    error: ErrorStats
    cost: CostStats
    nonfinite_pixels: int
    notes: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return asdict(self)


def _finite_stats(name: str, unit: str, values: np.ndarray) -> QuantityStats:
    v = values[np.isfinite(values)]
    if v.size == 0:
        return QuantityStats(name, unit, math.nan, math.nan, math.nan)
    return QuantityStats(name, unit, float(v.mean()), float(v.min()), float(v.max()))


def relative_error(mean: np.ndarray, sigma: np.ndarray) -> np.ndarray:
    """σ/|μ|, NaN where μ is zero or either value isn't finite."""
    with np.errstate(divide="ignore", invalid="ignore"):
        rel = sigma / np.abs(mean)
    return np.where(np.isfinite(rel) & (mean != 0), rel, math.nan)


def _worst_tiles(rel: np.ndarray, tiles: int, keep: int = 5) -> list[dict[str, float | int]]:
    height, width = rel.shape
    out = []
    ys = np.linspace(0, height, min(tiles, height) + 1, dtype=int)
    xs = np.linspace(0, width, min(tiles, width) + 1, dtype=int)
    for ty in range(len(ys) - 1):
        for tx in range(len(xs) - 1):
            block = rel[ys[ty] : ys[ty + 1], xs[tx] : xs[tx + 1]]
            block = block[np.isfinite(block)]
            if block.size:
                out.append({
                    "x0": int(xs[tx]), "y0": int(ys[ty]), "x1": int(xs[tx + 1]), "y1": int(ys[ty + 1]),
                    "p95": float(np.percentile(block, 95)),
                })
    out.sort(key=lambda t: t["p95"], reverse=True)
    return out[:keep]


def compute_stats(
    img: HtrdrImage,
    *,
    spp: int,
    spectral: Spectral | None = None,
    target_rel_err: float | None = None,
    tiles: int = 8,
) -> ImageStats:
    """Summarize a render taken at `spp` samples per pixel.

    Pass the scene's `spectral` when known: it fixes the layout (a black
    CIE XYZ image would otherwise look like shortwave) and, for monochromatic
    longwave runs, lets the brightness temperature be recomputed from the
    radiance, since htrdr 0.12 gets it wrong there.
    """
    layout: Layout = {"cie_xyz": "xyz", "lw": "longwave", "sw": "shortwave"}[spectral.mode] if spectral else img.layout()
    notes: list[str] = []
    quantities = [_finite_stats(name, unit, img.mean(pair)) for pair, name, unit in _QUANTITIES[layout]]

    if layout == "longwave" and spectral is not None and spectral.monochromatic and spectral.range_nm:
        lam = spectral.range_nm[0]
        tb = brightness_temperature(lam, img.mean(1))
        tb_sigma = brightness_temperature_sigma(lam, tb, img.sigma(1))
        quantities[0] = _finite_stats("brightness temperature", "K", tb)
        reported = img.mean(0)[np.isfinite(tb)]
        if reported.size and abs(float(np.median(reported)) - float(np.nanmedian(tb))) > 1.0:
            notes.append(
                f"htrdr's brightness temperature (median {np.median(reported):.1f} K) disagrees with its radiance "
                f"(median {np.nanmedian(tb):.1f} K at {lam:g} nm); reporting the value recomputed from the radiance."
            )
        rel_tb = relative_error(tb, tb_sigma)
        if np.isfinite(rel_tb).any():
            notes.append(f"Brightness temperature relative error: median {np.nanmedian(rel_tb):.3%}.")

    mean = img.mean(_TARGET_PAIR)
    rel = relative_error(mean, img.sigma(_TARGET_PAIR))
    valid = rel[np.isfinite(rel)]
    target_name = next(name for pair, name, _ in _QUANTITIES[layout] if pair == _TARGET_PAIR)
    if valid.size:
        pct = {f"p{p}": float(np.percentile(valid, p)) for p in PERCENTILES}
        rel_max = float(valid.max())
    else:
        pct = {f"p{p}": math.nan for p in PERCENTILES}
        rel_max = math.nan
        notes.append(f"No pixel has a finite, non-zero {target_name}; the error can't be estimated.")
    error = ErrorStats(
        quantity=target_name,
        percentiles=pct,
        max=rel_max,
        pixels=int(valid.size),
        target=target_rel_err,
        fraction_meeting_target=float(np.mean(valid <= target_rel_err)) if target_rel_err and valid.size else None,
        worst_tiles=_worst_tiles(rel, tiles),
    )

    paths_per_pixel = spp * (3 if layout == "xyz" else 1)
    time = img.mean(TIME_PAIR)
    finite_time = time[np.isfinite(time)]
    cost = CostStats(
        mean_time_per_path_us=float(finite_time.mean()) if finite_time.size else math.nan,
        paths=int(img.width * img.height * paths_per_pixel),
        cpu_s=float(finite_time.sum() * paths_per_pixel * 1e-6),
    )

    nonfinite = int(np.count_nonzero(~np.isfinite(img.data[:, :, :6]).all(axis=2)))
    if nonfinite:
        notes.append(f"{nonfinite} pixels hold non-finite values.")
    return ImageStats(img.width, img.height, layout, spp, quantities, error, cost, nonfinite, notes)
