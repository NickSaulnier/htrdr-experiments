"""Minimal PPM reading and PNG writing, so postprocess needs no imaging library.

htpp writes plain-text PPM (P3); people and the viewer want PNG.
"""

from __future__ import annotations

import struct
import zlib
from pathlib import Path

import numpy as np


def read_ppm(path: Path | str) -> np.ndarray:
    """A P3 PPM as an (height, width, 3) uint8 array."""
    text = Path(path).read_text()
    tokens = [t for line in text.splitlines() for t in line.split("#", 1)[0].split()]
    if not tokens or tokens[0] != "P3":
        raise ValueError(f"{path}: not a P3 PPM")
    width, height, maxval = (int(t) for t in tokens[1:4])
    if maxval != 255:
        raise ValueError(f"{path}: unsupported PPM maximum {maxval}")
    values = np.array(tokens[4:], dtype=np.uint8)
    if values.size != width * height * 3:
        raise ValueError(f"{path}: {values.size} values, expected {width * height * 3}")
    return values.reshape(height, width, 3)


def _chunk(kind: bytes, data: bytes) -> bytes:
    return struct.pack(">I", len(data)) + kind + data + struct.pack(">I", zlib.crc32(kind + data))


def png_bytes(rgb: np.ndarray) -> bytes:
    """An 8-bit RGB PNG of an (height, width, 3) uint8 array."""
    if rgb.dtype != np.uint8 or rgb.ndim != 3 or rgb.shape[2] != 3:
        raise ValueError("expected an (height, width, 3) uint8 array")
    height, width, _ = rgb.shape
    # Filter type 0 (none) at the start of every row.
    raw = np.concatenate([np.zeros((height, 1), np.uint8), rgb.reshape(height, width * 3)], axis=1).tobytes()
    header = struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0)
    return b"\x89PNG\r\n\x1a\n" + _chunk(b"IHDR", header) + _chunk(b"IDAT", zlib.compress(raw, 6)) + _chunk(b"IEND", b"")


def ppm_to_png(ppm: Path | str, png: Path | str) -> tuple[int, int]:
    rgb = read_ppm(ppm)
    Path(png).write_bytes(png_bytes(rgb))
    return rgb.shape[1], rgb.shape[0]
