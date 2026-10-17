"""What to render: a typed description of one htrdr-atmosphere scene.

A SceneSpec says everything about a render except how many samples to
take; the spp is chosen per render, which is what lets the agent preview
cheaply and then refine. Validation rejects what htrdr would reject, and
`warnings()` flags what htrdr accepts but probably isn't what was meant.
"""

from __future__ import annotations

import hashlib
import math
from pathlib import Path
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

Vec3 = tuple[float, float, float]


class _Model(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")


class Spectral(_Model):
    """htrdr's -s option."""

    mode: Literal["cie_xyz", "lw", "sw"] = "cie_xyz"
    """CIE XYZ (visible, 380–780 nm), longwave (thermal emission) or shortwave (solar)."""
    range_nm: tuple[float, float] | None = None
    """Wavelength range for lw and sw, in nm. Equal bounds mean monochromatic."""
    tref_k: float | None = Field(default=None, gt=0)
    """Reference Planck temperature; htrdr defaults to 290 K (lw) and 5778 K (sw)."""

    @model_validator(mode="after")
    def _check(self) -> Spectral:
        if self.mode == "cie_xyz":
            if self.range_nm is not None or self.tref_k is not None:
                raise ValueError("cie_xyz takes no wavelength range or Tref")
        else:
            if self.range_nm is None:
                raise ValueError(f"{self.mode} needs a wavelength range in nm")
            lo, hi = self.range_nm
            if not 0 < lo <= hi:
                raise ValueError(f"invalid wavelength range {lo}–{hi} nm")
        return self

    @property
    def monochromatic(self) -> bool:
        return self.range_nm is not None and self.range_nm[0] == self.range_nm[1]


class Sun(_Model):
    """htrdr's -D option."""

    azimuth_deg: float = Field(ge=0, lt=360)
    elevation_deg: float = Field(ge=0, le=90)


def _sub(a: Vec3, b: Vec3) -> Vec3:
    return (a[0] - b[0], a[1] - b[1], a[2] - b[2])


def _cross(a: Vec3, b: Vec3) -> Vec3:
    return (a[1] * b[2] - a[2] * b[1], a[2] * b[0] - a[0] * b[2], a[0] * b[1] - a[1] * b[0])


def _norm(a: Vec3) -> float:
    return math.sqrt(a[0] ** 2 + a[1] ** 2 + a[2] ** 2)


class _Oriented(_Model):
    pos: Vec3
    tgt: Vec3
    up: Vec3 = (0.0, 0.0, 1.0)

    @model_validator(mode="after")
    def _check_frame(self) -> _Oriented:
        view = _sub(self.tgt, self.pos)
        if _norm(view) == 0:
            raise ValueError("target and position coincide")
        if _norm(self.up) == 0 or _norm(_cross(view, self.up)) < 1e-9 * _norm(view) * _norm(self.up):
            raise ValueError("up vector is zero or parallel to the view direction")
        return self

    @property
    def view_elevation_deg(self) -> float:
        """Angle of the view direction above the horizontal."""
        view = _sub(self.tgt, self.pos)
        return math.degrees(math.asin(max(-1.0, min(1.0, view[2] / _norm(view)))))


class PerspectiveCamera(_Oriented):
    """htrdr's -C option (pinhole)."""

    kind: Literal["perspective"] = "perspective"
    fov_deg: float = Field(default=70.0, gt=0, lt=180)
    """Vertical field of view."""


class OrthographicCamera(_Oriented):
    """htrdr's -P option."""

    kind: Literal["orthographic"] = "orthographic"
    height_m: float = Field(gt=0)
    """Height of the image plane; the width follows the image's aspect ratio."""


class FluxSensor(_Oriented):
    """htrdr's -p option: flux through a rectangle, outside any geometry."""

    kind: Literal["flux"] = "flux"
    up: Vec3 = (0.0, 1.0, 0.0)
    size_m: tuple[float, float] = (1.0, 1.0)


Sensor = Annotated[PerspectiveCamera | OrthographicCamera | FluxSensor, Field(discriminator="kind")]


class Inputs(_Model):
    """htrdr's input files. Only the gas optical properties are required."""

    atmosphere: Path
    """Gas optical properties (htgop), -a."""
    clouds: Path | None = None
    """Cloud properties (htcp), -c. Needs `mie`."""
    mie: Path | None = None
    """Water droplet optical properties (htmie), -m."""
    ground: Path | None = None
    """Ground geometry (htrdr-obj), -g."""
    materials: Path | None = None
    """Ground materials (htrdr-materials), -M."""
    repeat_clouds: bool = False
    repeat_ground: bool = False
    sky_material: str | None = None

    @model_validator(mode="after")
    def _check(self) -> Inputs:
        if self.clouds is not None and self.mie is None:
            raise ValueError("clouds need droplet optical properties (mie)")
        if self.repeat_clouds and self.clouds is None:
            raise ValueError("repeat_clouds without clouds")
        if self.repeat_ground and self.ground is None:
            raise ValueError("repeat_ground without a ground")
        return self


class ImageDef(_Model):
    width: int = Field(default=320, ge=1)
    height: int = Field(default=240, ge=1)


class SceneSpec(_Model):
    inputs: Inputs
    spectral: Spectral = Spectral()
    sun: Sun | None = None
    sensor: Sensor
    image: ImageDef = ImageDef()

    def warnings(self) -> list[str]:
        """Things htrdr accepts that probably aren't what was meant."""
        out: list[str] = []
        if self.spectral.mode == "lw" and self.sun is not None:
            out.append(
                "The sun has no effect in lw mode: htrdr computes thermal emission only. "
                "Use sw for a solar view, or drop the sun."
            )
        if self.spectral.mode in ("sw", "cie_xyz") and self.sun is None:
            out.append("No sun direction given; htrdr's default will be used.")
        if self.spectral.mode == "lw" and self.spectral.monochromatic:
            out.append(
                "htrdr 0.12's brightness temperature is wrong for monochromatic lw runs "
                "(off by 1e9 in radiance); read_stats recomputes it from the radiance."
            )
        if self.inputs.ground is not None and self.sensor.pos[2] < 0:
            out.append("The sensor is below z = 0, under the ground.")
        if self.inputs.ground is None and self.sensor.kind != "flux" and self.sensor.view_elevation_deg < 0:
            out.append("The view points downward but there is no ground: rays below the horizon only see the atmosphere.")
        return out

    def cache_key(self) -> str:
        """Identifies the acceleration structures htrdr caches with -O.

        They depend on the input files and the spectral range, not on the
        sensor, sun or image, so renders that only move the camera or change
        the spp can share one cache.
        """
        payload = self.inputs.model_dump_json() + self.spectral.model_dump_json()
        return hashlib.sha256(payload.encode()).hexdigest()[:16]
