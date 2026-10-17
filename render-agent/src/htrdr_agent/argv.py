"""htrdr-atmosphere's command line, built from a SceneSpec."""

from __future__ import annotations

import shlex
from pathlib import Path

from .scene import FluxSensor, OrthographicCamera, PerspectiveCamera, SceneSpec, Vec3


def num(x: float) -> str:
    """A number as htrdr should see it: integers bare, others in short form."""
    if float(x).is_integer() and abs(x) < 1e15:
        return str(int(x))
    return f"{x:.12g}"


def vec(v: Vec3) -> str:
    return ",".join(num(c) for c in v)


def spectral_arg(spec: SceneSpec) -> str:
    s = spec.spectral
    if s.mode == "cie_xyz":
        return "cie_xyz"
    assert s.range_nm is not None
    arg = f"{s.mode}={num(s.range_nm[0])},{num(s.range_nm[1])}"
    if s.tref_k is not None:
        arg += f":Tref={num(s.tref_k)}"
    return arg


def sensor_args(spec: SceneSpec) -> list[str]:
    s = spec.sensor
    frame = f"pos={vec(s.pos)}:tgt={vec(s.tgt)}:up={vec(s.up)}"
    match s:
        case PerspectiveCamera():
            return ["-C", f"{frame}:fov={num(s.fov_deg)}"]
        case OrthographicCamera():
            return ["-P", f"{frame}:height={num(s.height_m)}"]
        case FluxSensor():
            return ["-p", f"{frame}:sz={num(s.size_m[0])},{num(s.size_m[1])}"]


def build_argv(
    spec: SceneSpec,
    *,
    spp: int,
    output: Path | str,
    binary: Path | str = "htrdr-atmosphere",
    cache: Path | str | None = None,
    threads: int | None = None,
    verbose: bool = False,
) -> list[str]:
    """The full argv for one render, binary first.

    Always passes -f: outputs live in per-render run directories, so
    overwriting means the same render is being redone.
    """
    if spp < 1:
        raise ValueError("spp must be at least 1")
    i = spec.inputs
    argv = [str(binary), "-a", str(i.atmosphere)]
    if i.clouds is not None:
        argv += ["-c", str(i.clouds), "-m", str(i.mie)]
    if i.ground is not None:
        argv += ["-g", str(i.ground)]
    if i.materials is not None:
        argv += ["-M", str(i.materials)]
    if i.repeat_ground:
        argv.append("-R")
    if i.repeat_clouds:
        argv.append("-r")
    if i.sky_material is not None:
        argv += ["-n", i.sky_material]
    argv += ["-s", spectral_arg(spec)]
    if spec.sun is not None:
        argv += ["-D", f"{num(spec.sun.azimuth_deg)},{num(spec.sun.elevation_deg)}"]
    argv += sensor_args(spec)
    argv += ["-i", f"def={spec.image.width}x{spec.image.height}:spp={spp}"]
    if cache is not None:
        argv += ["-O", str(cache)]
    if threads is not None:
        argv += ["-t", str(threads)]
    if verbose:
        argv.append("-v")
    argv += ["-f", "-o", str(output)]
    return argv


def command_line(argv: list[str]) -> str:
    """The argv as one shell-safe line, as written to run logs."""
    return shlex.join(argv)
