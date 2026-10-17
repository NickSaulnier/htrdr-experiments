"""A stand-in for htrdr-atmosphere, for testing without htrdr.

Accepts htrdr-atmosphere's command line and writes a valid htrdr-image:

- The scene is a plane-parallel sky, so values depend on each pixel's ray
  elevation: brighter (CIE XYZ, sw) or warmer (lw) toward the horizon.
- Each pixel's estimate carries Gaussian noise with σ ∝ 1/√spp, at relative
  noise levels close to htrdr's on the same views (about 2 % at 16 spp for
  monochromatic lw, 40 % at 32 spp for CIE XYZ), so spp extrapolation can be
  tested end to end.
- The time pair is a realistic per-path cost, and the process sleeps in
  proportion to the work, scaled by FAKE_HTRDR_SPEED (default 1e-4 of real
  time) so tests stay fast.
- Like htrdr 0.12, monochromatic lw brightness temperatures are wrong by a
  factor of 1e9 in radiance.

Environment knobs for tests: FAKE_HTRDR_SPEED, FAKE_HTRDR_SLEEP (fixed
sleep in seconds), FAKE_HTRDR_FAIL (exit 1 with an error message).
Output is deterministic for a given command line.
"""

from __future__ import annotations

import argparse
import hashlib
import math
import os
import sys
import time
from pathlib import Path

import numpy as np

from .image import brightness_temperature, planck_nm

# Relative noise of a single path's estimate, per spectral mode.
PATH_NOISE = {"cie_xyz": 2.3, "lw_mono": 0.08, "lw_band": 0.4, "sw": 1.5}
PATH_TIME_US = 300.0


def _opts(text: str) -> dict[str, str]:
    out = {}
    for part in text.split(":"):
        key, _, value = part.partition("=")
        out[key] = value
    return out


def _vec(text: str) -> np.ndarray:
    return np.array([float(c) for c in text.split(",")], dtype=np.float64)


class _Parser(argparse.ArgumentParser):
    def error(self, message: str) -> None:  # htrdr's style: message on stderr, exit 1
        print(f"htrdr-atmosphere: {message}", file=sys.stderr)
        sys.exit(1)


def _parser() -> argparse.ArgumentParser:
    p = _Parser(prog="htrdr-atmosphere", add_help=False)
    p.add_argument("-a")
    p.add_argument("-c")
    p.add_argument("-m")
    p.add_argument("-g")
    p.add_argument("-M")
    p.add_argument("-n")
    p.add_argument("-O")
    p.add_argument("-o")
    p.add_argument("-s", default="cie_xyz")
    p.add_argument("-D")
    p.add_argument("-C")
    p.add_argument("-P")
    p.add_argument("-p")
    p.add_argument("-i", default="")
    p.add_argument("-t", type=int)
    p.add_argument("-T")
    p.add_argument("-V")
    for flag in ("-f", "-R", "-r", "-v", "-d", "-h"):
        p.add_argument(flag, action="store_true")
    return p


def _ray_elevations(camera: dict[str, str], width: int, height: int) -> np.ndarray:
    """Elevation (radians) of each pixel's ray for a perspective camera."""
    pos = _vec(camera.get("pos", "0,0,0"))
    tgt = _vec(camera.get("tgt", "0,1,0"))
    up = _vec(camera.get("up", "0,0,1"))
    fov = math.radians(float(camera.get("fov", "70")))
    f = (tgt - pos) / np.linalg.norm(tgt - pos)
    r = np.cross(f, up)
    r /= np.linalg.norm(r)
    u = np.cross(r, f)
    half_h = math.tan(fov / 2)
    half_w = half_h * width / height
    ys = (1 - 2 * (np.arange(height) + 0.5) / height) * half_h  # row 0 at the top
    xs = (2 * (np.arange(width) + 0.5) / width - 1) * half_w
    d = f + xs[None, :, None] * r + ys[:, None, None] * u
    d /= np.linalg.norm(d, axis=2, keepdims=True)
    return np.arcsin(np.clip(d[:, :, 2], -1, 1))


def render(args: argparse.Namespace, seed: int) -> np.ndarray:
    image = _opts(args.i) if args.i else {}
    width, height = (int(v) for v in image.get("def", "320x240").split("x"))
    spp = int(image.get("spp", "1"))
    camera = _opts(args.C) if args.C else {}
    elev = _ray_elevations(camera, width, height)
    # Rays near the horizon cross the most air.
    airmass = 1 / np.maximum(np.abs(np.sin(elev)), 0.05)

    mode, _, rest = args.s.partition("=")
    rng = np.random.default_rng(seed)
    data = np.zeros((height, width, 8))

    def estimate(pair: int, truth: np.ndarray, noise: float, paths: int) -> None:
        sigma = np.abs(truth) * noise / math.sqrt(paths)
        data[:, :, 2 * pair] = truth + sigma * rng.standard_normal(truth.shape)
        data[:, :, 2 * pair + 1] = sigma

    if mode == "lw":
        lo, hi = (float(v) for v in rest.split(":")[0].split(","))
        tb = 210 + 70 * (1 - np.exp(-airmass / 8))  # cold zenith, warm horizon
        if lo == hi:
            estimate(1, planck_nm(lo, tb), PATH_NOISE["lw_mono"], spp)
            # htrdr 0.12's bug: Tb inverted from radiance taken as per metre.
            data[:, :, 0] = brightness_temperature(lo, data[:, :, 2] * 1e-9)
            data[:, :, 1] = data[:, :, 0] * PATH_NOISE["lw_mono"] / math.sqrt(spp) / 4
        else:
            band = sum(planck_nm(lam, tb) for lam in np.linspace(lo, hi, 9)) / 9 * (hi - lo)
            estimate(1, band, PATH_NOISE["lw_band"], spp)
            estimate(0, tb, PATH_NOISE["lw_band"] / 4, spp)
        paths = spp
    elif mode == "sw":
        estimate(1, 0.02 * (1 + airmass / 4), PATH_NOISE["sw"], spp)
        paths = spp
    else:
        y = 5 * (1 + airmass / 4)
        estimate(0, y * 0.9, PATH_NOISE["cie_xyz"], spp)
        estimate(1, y, PATH_NOISE["cie_xyz"], spp)
        estimate(2, y * 1.6, PATH_NOISE["cie_xyz"], spp)
        paths = 3 * spp
    per_path = PATH_TIME_US * (0.8 + 0.4 * rng.random((height, width)))
    estimate(3, per_path, 0.5, paths)
    data[:, :, 6] = np.abs(data[:, :, 6])

    work_s = float(per_path.sum()) * paths * 1e-6 / max(args.t or os.cpu_count() or 1, 1)
    time.sleep(float(os.environ.get("FAKE_HTRDR_SLEEP", work_s * float(os.environ.get("FAKE_HTRDR_SPEED", "1e-4")))))
    return data


def _write(data: np.ndarray, out) -> None:
    height, width, _ = data.shape
    out.write(f"{width} {height}\n")
    np.savetxt(out, data.reshape(-1, 8), fmt="%g")


def main(argv: list[str] | None = None) -> int:
    argv = sys.argv[1:] if argv is None else argv
    args = _parser().parse_args(argv)
    # Deterministic per command line, like a fixed-seed htrdr. File locations
    # (-a, -c, -m, -g, -M, -O, -o) don't change the physics, so they're left
    # out: the same scene renders the same wherever its files are.
    path_flags = {"-a", "-c", "-m", "-g", "-M", "-O", "-o"}
    key = [a for a, prev in zip(argv, [None, *argv]) if a not in path_flags and prev not in path_flags]
    seed = int.from_bytes(hashlib.sha256(" ".join(key).encode()).digest()[:8], "little")
    if args.h:
        print("usage: htrdr-atmosphere [-dfhRrv] ... (fake, see htrdr_agent.fake)")
        return 0
    if os.environ.get("FAKE_HTRDR_FAIL"):
        print("htrdr-atmosphere: simulated failure (FAKE_HTRDR_FAIL)", file=sys.stderr)
        return 1
    if not args.a:
        print("htrdr-atmosphere: missing the gas optical properties (-a)", file=sys.stderr)
        return 1
    if not Path(args.a).is_file():
        print(f"htrdr-atmosphere: cannot open `{args.a}'", file=sys.stderr)
        return 1
    if args.P or args.p:
        print("htrdr-atmosphere: the fake only renders perspective cameras (-C)", file=sys.stderr)
        return 1
    if args.o and Path(args.o).exists() and not args.f:
        print(f"htrdr-atmosphere: `{args.o}' already exists; use -f to overwrite it", file=sys.stderr)
        return 1
    if args.O:
        Path(args.O).touch()
    data = render(args, seed)
    if args.o:
        with open(args.o, "w") as out:
            _write(data, out)
    else:
        _write(data, sys.stdout)
    return 0


if __name__ == "__main__":
    sys.exit(main())
