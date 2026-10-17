"""What the LLM extracts from a natural-language render request, and how
code turns it into a SceneSpec.

The LLM fills in `RenderRequest`, a flat schema in the units people use:
micrometres, metres, degrees of view elevation and heading, a target in
percent. It never writes file paths (inputs come from configuration) or
camera vectors (computed here from the angles), so its job stays small and
the unit conversions stay deterministic and tested.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from .scene import ImageDef, Inputs, PerspectiveCamera, SceneSpec, Spectral, Sun, Vec3

DEFAULT_TARGET_PERCENT = 1.0
QUALITY_TARGET_PERCENT = {"preview": 5.0, "high_quality": 0.5}
LOOK_DISTANCE_M = 1000.0
_VERTICAL_DEG = 89.0  # beyond this the view is "straight up/down" and needs another up vector


class RenderRequest(BaseModel):
    """A render request, as read from what someone asked for."""

    model_config = ConfigDict(extra="forbid")

    # Every field is required (nullable where it may be absent), so guided
    # decoding always produces a complete object.
    #
    # The spectral fields record what the text says, not a decision: code
    # applies the conventions (resolve_spectral), because a 9B model doesn't
    # apply them reliably when the wording points the other way ("infrared
    # at 3.5 µm", "a colour photo at midday").
    spectral_words: Literal["none", "visible", "infrared", "solar"] = Field(
        description=(
            "The kind of light the request names in words, ignoring any numbers: "
            "visible ('visible', 'colour', 'photo', 'picture', 'what the eye sees'), "
            "infrared ('infrared', 'IR', 'thermal', 'brightness temperature'), "
            "solar ('solar', 'shortwave', 'sunlight'), or none. Words about the sun's position or the time of "
            "day ('sunset', 'noon') are not light words."
        )
    )
    wavelength_min_um: float | None = Field(
        description="The lower bound of a wavelength or band the request gives as a number, in micrometres "
        "(10000 nm = 10 µm). A single wavelength gives min = max. Null if no number is given."
    )
    wavelength_max_um: float | None = Field(description="The upper bound in micrometres; equal to min for a single wavelength; null if none.")
    sun_elevation_deg: float | None = Field(description="Sun elevation above the horizon, 0-90 degrees; null if no sun is mentioned.")
    sun_azimuth_deg: float | None = Field(description="Sun azimuth, 0-360 degrees; null if not given.")
    camera_altitude_m: float = Field(description="Camera height above the ground in metres (e.g. '2 km' is 2000).")
    view_elevation_deg: float = Field(
        description="Where the camera looks: -90 straight down, 0 at the horizon, +90 straight up."
    )
    view_azimuth_deg: float = Field(description="Camera heading, 0-360 degrees.")
    fov_deg: float = Field(description="Vertical field of view in degrees.")
    image_width: int = Field(description="Image width in pixels.")
    image_height: int = Field(description="Image height in pixels.")
    target_rel_err_percent: float | None = Field(
        description="An uncertainty the request gives as a number, as a percentage ('1%' is 1, '0.5 percent' "
        "is 0.5); null if no number is given."
    )
    quality_words: Literal["none", "preview", "high_quality"] = Field(
        description="Words about quality, ignoring numbers: preview ('quick', 'fast', 'rough', 'draft', "
        "'preview'), high_quality ('high quality', 'best', 'clean', 'publication'), or none."
    )
    time_budget_s: float | None = Field(description="Time limit for the whole job in seconds; null if not stated.")
    assumptions: list[str] = Field(
        description="One short entry per value that was chosen rather than read from the request."
    )
    is_render_request: bool = Field(
        description="True when the message asks for a render, an image or a view, however vague. False for "
        "anything else: questions, thanks, greetings, small talk."
    )
    clarification: str | None = Field(
        description="When is_render_request is false, one short reply or question; null otherwise."
    )


class RequestError(ValueError):
    """The request can't become a scene; the message says why, for the agent to relay."""


def _direction(elevation_deg: float, azimuth_deg: float) -> Vec3:
    el, az = math.radians(elevation_deg), math.radians(azimuth_deg)
    return (math.cos(el) * math.cos(az), math.cos(el) * math.sin(az), math.sin(el))


def camera_for(req: RenderRequest) -> PerspectiveCamera:
    """A perspective camera at the requested altitude, looking along the requested angles.

    Azimuth is measured in the horizontal plane from the +x axis toward +y.
    For views within 1° of straight up or down, "up" in the image points
    along the heading, since the vertical can't be up there.
    """
    if not -90 <= req.view_elevation_deg <= 90:
        raise RequestError(f"view elevation {req.view_elevation_deg}° is outside -90..90")
    if not 0 < req.fov_deg < 180:
        raise RequestError(f"field of view {req.fov_deg}° is outside 0..180")
    pos: Vec3 = (0.0, 0.0, float(req.camera_altitude_m))
    d = _direction(req.view_elevation_deg, req.view_azimuth_deg)
    tgt: Vec3 = tuple(round(p + LOOK_DISTANCE_M * c, 6) for p, c in zip(pos, d))  # type: ignore[assignment]
    up: Vec3 = (0.0, 0.0, 1.0)
    if abs(req.view_elevation_deg) > _VERTICAL_DEG:
        h = _direction(0.0, req.view_azimuth_deg)
        up = (round(h[0], 6), round(h[1], 6), 0.0)
    return PerspectiveCamera(pos=pos, tgt=tgt, up=up, fov_deg=req.fov_deg)


SpectralMode = Literal["visible", "thermal_infrared", "solar"]

THERMAL_CUTOFF_UM = 4.0
"""Wavelengths at or above this are rendered as thermal emission (htrdr lw), below it as sunlight (sw).
htrdr's modes are exclusive, so around 3-5 µm, where both matter, this convention decides."""
DEFAULT_THERMAL_BAND_UM = (8.0, 12.0)  # the atmospheric window
DEFAULT_SOLAR_BAND_UM = (0.4, 0.7)


@dataclass(frozen=True)
class ResolvedSpectral:
    mode: SpectralMode
    wavelength_min_um: float | None
    wavelength_max_um: float | None
    notes: list[str]
    """Conventions applied, worth adding to the request's assumptions."""


def resolve_spectral(req: RenderRequest) -> ResolvedSpectral:
    """Decide the spectral mode from what the request said.

    A wavelength decides by itself (THERMAL_CUTOFF_UM); words only matter
    when no number was given, and default to a visible image.
    """
    lo, hi = req.wavelength_min_um, req.wavelength_max_um
    if lo is None and hi is None:
        if req.spectral_words == "infrared":
            lo, hi = DEFAULT_THERMAL_BAND_UM
            return ResolvedSpectral("thermal_infrared", lo, hi, [f"infrared with no wavelength: the {lo:g}-{hi:g} µm window"])
        if req.spectral_words == "solar":
            lo, hi = DEFAULT_SOLAR_BAND_UM
            return ResolvedSpectral("solar", lo, hi, [f"solar with no wavelength: {lo:g}-{hi:g} µm"])
        return ResolvedSpectral("visible", None, None, [])
    lo = hi if lo is None else lo
    hi = lo if hi is None else hi
    lo, hi = sorted((lo, hi))  # type: ignore[type-var]
    if lo <= 0:
        raise RequestError(f"invalid wavelength {lo} µm")
    thermal = lo >= THERMAL_CUTOFF_UM
    if not thermal and hi >= THERMAL_CUTOFF_UM:
        raise RequestError(f"the band {lo:g}-{hi:g} µm straddles {THERMAL_CUTOFF_UM:g} µm; split it into a solar and a thermal render")
    mode: SpectralMode = "thermal_infrared" if thermal else "solar"
    notes = []
    if req.spectral_words == "infrared" and not thermal:
        notes.append(f"{lo:g} µm is below {THERMAL_CUTOFF_UM:g} µm, so it is rendered with sunlight only (htrdr sw), not thermal emission")
    elif req.spectral_words in ("solar", "visible") and thermal:
        notes.append(f"{lo:g} µm is {THERMAL_CUTOFF_UM:g} µm or more, so it is rendered as thermal emission (htrdr lw), not sunlight")
    return ResolvedSpectral(mode, lo, hi, notes)


def spectral_for(req: RenderRequest) -> Spectral:
    r = resolve_spectral(req)
    if r.mode == "visible":
        return Spectral(mode="cie_xyz")
    assert r.wavelength_min_um is not None and r.wavelength_max_um is not None
    mode = "lw" if r.mode == "thermal_infrared" else "sw"
    return Spectral(mode=mode, range_nm=(round(r.wavelength_min_um * 1000, 6), round(r.wavelength_max_um * 1000, 6)))


def to_scene_spec(req: RenderRequest, inputs: Inputs) -> SceneSpec:
    """The SceneSpec for a parsed request. Raises RequestError when it can't be built."""
    if not req.is_render_request:
        raise RequestError(f"not a render request: {req.clarification or 'nothing to render'}")
    sun = None
    if req.sun_elevation_deg is not None:
        az = req.sun_azimuth_deg if req.sun_azimuth_deg is not None else 180.0
        if not 0 <= req.sun_elevation_deg <= 90:
            raise RequestError(f"sun elevation {req.sun_elevation_deg}° is outside 0..90 (htrdr needs the sun above the horizon)")
        sun = Sun(azimuth_deg=az % 360, elevation_deg=req.sun_elevation_deg)
    if req.image_width < 1 or req.image_height < 1:
        raise RequestError(f"invalid image size {req.image_width}x{req.image_height}")
    try:
        return SceneSpec(
            inputs=inputs,
            spectral=spectral_for(req),
            sun=sun,
            sensor=camera_for(req),
            image=ImageDef(width=req.image_width, height=req.image_height),
        )
    except ValueError as e:  # pydantic's ValidationError is a ValueError
        raise RequestError(str(e)) from e


def target_percent(req: RenderRequest) -> float:
    """The requested uncertainty in percent: a stated number wins, then quality words, then 1 %."""
    if req.target_rel_err_percent is not None:
        return req.target_rel_err_percent
    return QUALITY_TARGET_PERCENT.get(req.quality_words, DEFAULT_TARGET_PERCENT)


def target_rel_err(req: RenderRequest) -> float:
    """The requested uncertainty as a fraction (see target_percent)."""
    pct = target_percent(req)
    if not 0 < pct < 100:
        raise RequestError(f"target uncertainty {pct}% is outside 0..100")
    return pct / 100
