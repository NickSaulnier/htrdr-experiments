"""Read htrdr-image(5) files: the format htrdr writes.

    <width> <height>
    one line per pixel, row-major from the top-left: 8 reals, i.e. four
    (mean, standard error) pairs

`#` starts a comment and blank lines are skipped. Like htpp, a pixel line
may carry only 6 values, the time pair then being 0. This is the same
format the browser viewer reads (src/format/parse.ts); the tests share its
fixtures.
"""

from __future__ import annotations

import io
import math
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

import numpy as np

PIXEL_VALUES = 8
MIN_VALUES = 6

Layout = Literal["xyz", "longwave", "shortwave"]


class HtrdrImageError(ValueError):
    def __init__(self, message: str, line: int = 0):
        super().__init__(f"line {line}: {message}" if line else message)
        self.line = line


@dataclass(frozen=True)
class HtrdrImage:
    width: int
    height: int
    data: np.ndarray
    """shape (height, width, 8), float64: row 0 is the top of the image."""

    def mean(self, pair: int) -> np.ndarray:
        return self.data[:, :, 2 * pair]

    def sigma(self, pair: int) -> np.ndarray:
        return self.data[:, :, 2 * pair + 1]

    def layout(self) -> Layout:
        """Guess the spectral layout from which pairs are zero.

        lw and sw leave pair 2 unused, and sw also pair 0. A black CIE XYZ
        image looks like sw; callers that know the spectral mode should
        prefer it.
        """
        def zero(pair: int) -> bool:
            return not np.any(self.data[:, :, 2 * pair : 2 * pair + 2])

        if not zero(2):
            return "xyz"
        return "shortwave" if zero(0) else "longwave"


def _strip_comment(line: str) -> str:
    cut = line.find("#")
    return (line if cut < 0 else line[:cut]).strip()


def _header(lines: list[str]) -> tuple[int, int, int]:
    """Width, height and the index of the line after the definition."""
    for i, raw in enumerate(lines):
        text = _strip_comment(raw)
        if not text:
            continue
        tokens = text.split()
        if len(tokens) != 2 or not all(t.isdigit() for t in tokens):
            raise HtrdrImageError(f'expected the image definition "<width> <height>", found "{text}"', i + 1)
        width, height = int(tokens[0]), int(tokens[1])
        if width == 0 or height == 0:
            raise HtrdrImageError(f"invalid image definition {width}x{height}", i + 1)
        return width, height, i + 1
    raise HtrdrImageError("missing image definition")


def _parse_lines(lines: list[str], first: int, count: int) -> np.ndarray:
    """The careful path: per-line parsing with line numbers in errors."""
    out = np.zeros((count, PIXEL_VALUES))
    n = 0
    for i in range(first, len(lines)):
        text = _strip_comment(lines[i])
        if not text:
            continue
        tokens = text.split()
        if not MIN_VALUES <= len(tokens) <= PIXEL_VALUES:
            raise HtrdrImageError(f"expected {MIN_VALUES} to {PIXEL_VALUES} values per pixel, found {len(tokens)}", i + 1)
        if n == count:
            raise HtrdrImageError(f"more pixels than the definition allows ({count})", i + 1)
        try:
            # float() accepts C's printf spellings: nan, -nan, inf, -inf.
            out[n, : len(tokens)] = [float(t) for t in tokens]
        except ValueError:
            bad = next(t for t in tokens if not _is_number(t))
            raise HtrdrImageError(f'invalid number "{bad}"', i + 1) from None
        n += 1
    if n != count:
        raise HtrdrImageError(f"truncated image: expected {count} pixels, found {n}")
    return out


def _is_number(token: str) -> bool:
    try:
        float(token)
        return True
    except ValueError:
        return False


def parse(source: str | bytes) -> HtrdrImage:
    """Parse htrdr-image text. Raises HtrdrImageError on malformed input."""
    text = source.decode() if isinstance(source, bytes) else source
    lines = text.splitlines()
    width, height, first = _header(lines)
    count = width * height
    body = "\n".join(lines[first:])
    try:
        # numpy's C parser: fast, and right whenever every line has 8 values.
        data = np.loadtxt(io.StringIO(body), comments="#", ndmin=2, dtype=np.float64)
        if data.shape != (count, PIXEL_VALUES):
            raise ValueError("shape")
    except ValueError:
        data = _parse_lines(lines, first, count)
    return HtrdrImage(width, height, data.reshape(height, width, PIXEL_VALUES))


def load(path: Path | str) -> HtrdrImage:
    return parse(Path(path).read_text())


# --- Planck ---------------------------------------------------------------

_H = 6.62607015e-34
_C = 2.99792458e8
_K = 1.380649e-23


def planck_nm(wavelength_nm: float, temperature_k: np.ndarray | float) -> np.ndarray | float:
    """Spectral radiance in W/sr/m²/nm."""
    lam = wavelength_nm * 1e-9
    return 2 * _H * _C**2 / lam**5 / np.expm1(_H * _C / (lam * _K * np.asarray(temperature_k))) * 1e-9


def brightness_temperature(wavelength_nm: float, radiance_nm: np.ndarray) -> np.ndarray:
    """Invert Planck: radiance in W/sr/m²/nm to temperature in K. NaN where radiance ≤ 0."""
    lam = wavelength_nm * 1e-9
    radiance = np.asarray(radiance_nm, dtype=np.float64) * 1e9  # per m
    with np.errstate(divide="ignore", invalid="ignore"):
        t = _H * _C / (lam * _K) / np.log1p(2 * _H * _C**2 / (lam**5 * radiance))
    return np.where(radiance > 0, t, math.nan)


def brightness_temperature_sigma(wavelength_nm: float, temperature_k: np.ndarray, sigma_radiance_nm: np.ndarray) -> np.ndarray:
    """σ of the brightness temperature from σ of the radiance (first order: σ_T = σ_L / (dB/dT))."""
    lam = wavelength_nm * 1e-9
    t = np.asarray(temperature_k, dtype=np.float64)
    with np.errstate(divide="ignore", invalid="ignore", over="ignore"):
        x = _H * _C / (lam * _K * t)
        b = planck_nm(wavelength_nm, t)
        db_dt = b * x * np.exp(x) / np.expm1(x) / t
        return np.asarray(sigma_radiance_nm) / db_dt
