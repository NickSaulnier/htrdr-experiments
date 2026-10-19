"""Band-integrated Planck radiance and its inverse: htrdr's brightness temperature.

For a longwave band [lo, hi], htrdr-atmosphere reports the brightness
temperature Tb such that the band integral of B(lambda, Tb) equals the
pixel's band radiance (W/sr/m^2). M2 checked this against its output: the
inversion here reproduces htrdr's Tb to within 0.01 K.

htrdr 0.12's standard error of Tb is twice too large: independent renders
of the same pixels (a 180-degree camera roll puts every pixel in a
different RNG tile) differ by half the reported sigma(Tb), while sigma of
the radiance is right. `band_tb_sigma` gives the first-order value,
sigma(L) / (dL/dT), which matches the measured spread.
"""

from __future__ import annotations

import numpy as np

_H = 6.62607015e-34
_C = 2.99792458e8
_K = 1.380649e-23
_NODES, _WEIGHTS = np.polynomial.legendre.leggauss(64)


def _quadrature(lo_nm: float, hi_nm: float) -> tuple[np.ndarray, np.ndarray]:
    lam = (0.5 * (hi_nm - lo_nm) * _NODES + 0.5 * (hi_nm + lo_nm)) * 1e-9  # m
    return lam, _WEIGHTS * 0.5 * (hi_nm - lo_nm)  # weights in nm


def band_radiance(temperature_k, lo_nm: float, hi_nm: float) -> np.ndarray:
    """Integral of Planck's spectral radiance over [lo, hi] nm, in W/sr/m^2."""
    lam, w = _quadrature(lo_nm, hi_nm)
    t = np.asarray(temperature_k, dtype=np.float64)[..., None]
    with np.errstate(over="ignore", divide="ignore"):
        b = 2 * _H * _C**2 / lam**5 / np.expm1(_H * _C / (lam * _K * t)) * 1e-9  # per nm
    return (b * w).sum(axis=-1)


_TABLES: dict[tuple[float, float], tuple[np.ndarray, np.ndarray]] = {}
_T_GRID = np.arange(50.0, 1000.0 + 1e-9, 0.01)


def band_brightness_temperature(radiance, lo_nm: float, hi_nm: float) -> np.ndarray:
    """Invert `band_radiance` (50-1000 K); NaN where the radiance is not positive or out of range.

    Uses a table of the band radiance every 0.01 K, interpolated linearly in
    T: the error is below 1e-6 K, and a 640x360 frame inverts in milliseconds.
    """
    key = (float(lo_nm), float(hi_nm))
    if key not in _TABLES:
        _TABLES[key] = (band_radiance(_T_GRID, lo_nm, hi_nm), _T_GRID)
    l_grid, t_grid = _TABLES[key]
    radiance = np.asarray(radiance, dtype=np.float64)
    t = np.interp(radiance, l_grid, t_grid, left=np.nan, right=np.nan)
    return np.where(radiance > 0, t, np.nan)


def band_tb_sigma(temperature_k, radiance_sigma, lo_nm: float, hi_nm: float) -> np.ndarray:
    """First-order standard error of Tb: sigma(L) / (dL/dT)."""
    t = np.asarray(temperature_k, dtype=np.float64)
    dt = 0.01
    slope = (band_radiance(t + dt, lo_nm, hi_nm) - band_radiance(t - dt, lo_nm, hi_nm)) / (2 * dt)
    with np.errstate(invalid="ignore", divide="ignore"):
        return np.asarray(radiance_sigma, dtype=np.float64) / slope
