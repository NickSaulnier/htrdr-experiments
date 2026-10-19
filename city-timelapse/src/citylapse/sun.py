"""Solar position (NOAA's calculator) and its conversion to htrdr's -D.

The formulas are those of NOAA's Solar Calculator spreadsheet (after Meeus,
"Astronomical Algorithms"), good to about 0.01 degrees for years 1800-2100.
Elevation includes NOAA's approximate atmospheric refraction correction,
since that is where the sun appears; htrdr's atmosphere does not refract.

Scene axes: +X is east, +Y is north, +Z is up. Compass azimuth is measured
clockwise from north; htrdr's azimuth is counter-clockwise from +X, so
htrdr = 90 - compass (mod 360).
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import datetime, timezone


@dataclass(frozen=True)
class SolarPosition:
    elevation_deg: float  # apparent, with refraction
    azimuth_deg: float  # compass: clockwise from north
    declination_deg: float
    equation_of_time_min: float

    @property
    def htrdr_azimuth_deg(self) -> float:
        return to_htrdr_azimuth(self.azimuth_deg)


def to_htrdr_azimuth(compass_deg: float) -> float:
    """Compass azimuth (clockwise from north) to htrdr's (counter-clockwise from +X = east)."""
    return (90.0 - compass_deg) % 360.0


def julian_day(t: datetime) -> float:
    if t.tzinfo is None:
        raise ValueError("solar position needs an aware datetime")
    u = t.astimezone(timezone.utc)
    # Days since the Unix epoch, which is JD 2440587.5.
    return 2440587.5 + u.timestamp() / 86400.0


def _refraction_deg(elevation_deg: float) -> float:
    """NOAA's approximation of atmospheric refraction, in degrees."""
    e = elevation_deg
    if e > 85.0:
        return 0.0
    te = math.tan(math.radians(e))
    if e > 5.0:
        arcsec = 58.1 / te - 0.07 / te**3 + 0.000086 / te**5
    elif e > -0.575:
        arcsec = 1735.0 + e * (-518.2 + e * (103.4 + e * (-12.79 + e * 0.711)))
    else:
        arcsec = -20.772 / te
    return arcsec / 3600.0


def solar_position(t: datetime, latitude_deg: float, longitude_deg: float, *, refraction: bool = True) -> SolarPosition:
    """Sun position at time `t` (timezone-aware) seen from latitude/longitude (east positive)."""
    jc = (julian_day(t) - 2451545.0) / 36525.0  # Julian centuries since J2000

    geom_mean_long = (280.46646 + jc * (36000.76983 + jc * 0.0003032)) % 360.0
    geom_mean_anom = 357.52911 + jc * (35999.05029 - 0.0001537 * jc)
    ecc = 0.016708634 - jc * (0.000042037 + 0.0000001267 * jc)
    m = math.radians(geom_mean_anom)
    center = (math.sin(m) * (1.914602 - jc * (0.004817 + 0.000014 * jc))
              + math.sin(2 * m) * (0.019993 - 0.000101 * jc)
              + math.sin(3 * m) * 0.000289)
    true_long = geom_mean_long + center
    omega = math.radians(125.04 - 1934.136 * jc)
    app_long = true_long - 0.00569 - 0.00478 * math.sin(omega)
    mean_obliq = 23.0 + (26.0 + (21.448 - jc * (46.815 + jc * (0.00059 - jc * 0.001813))) / 60.0) / 60.0
    obliq = mean_obliq + 0.00256 * math.cos(omega)
    decl = math.degrees(math.asin(math.sin(math.radians(obliq)) * math.sin(math.radians(app_long))))

    y = math.tan(math.radians(obliq / 2.0)) ** 2
    l0 = math.radians(geom_mean_long)
    eot = 4.0 * math.degrees(
        y * math.sin(2 * l0)
        - 2 * ecc * math.sin(m)
        + 4 * ecc * y * math.sin(m) * math.cos(2 * l0)
        - 0.5 * y * y * math.sin(4 * l0)
        - 1.25 * ecc * ecc * math.sin(2 * m)
    )

    u = t.astimezone(timezone.utc)
    minutes_utc = u.hour * 60 + u.minute + (u.second + u.microsecond / 1e6) / 60.0
    true_solar_time = (minutes_utc + eot + 4.0 * longitude_deg) % 1440.0
    hour_angle = true_solar_time / 4.0 - 180.0 if true_solar_time >= 0 else true_solar_time / 4.0 + 180.0

    lat = math.radians(latitude_deg)
    d = math.radians(decl)
    cos_zen = math.sin(lat) * math.sin(d) + math.cos(lat) * math.cos(d) * math.cos(math.radians(hour_angle))
    zenith = math.degrees(math.acos(max(-1.0, min(1.0, cos_zen))))
    elevation = 90.0 - zenith

    # Azimuth from the spherical triangle, clockwise from north.
    sz = math.sin(math.radians(zenith))
    if abs(sz) < 1e-12:
        azimuth = 180.0
    else:
        c = (math.sin(lat) * math.cos(math.radians(zenith)) - math.sin(d)) / (math.cos(lat) * sz)
        a = math.degrees(math.acos(max(-1.0, min(1.0, c))))
        azimuth = (a + 180.0) % 360.0 if hour_angle > 0 else (540.0 - a) % 360.0

    if refraction:
        elevation += _refraction_deg(elevation)
    return SolarPosition(elevation, azimuth, decl, eot)


def htrdr_sun(pos: SolarPosition) -> tuple[float, float]:
    """(azimuth, elevation) for -D, or raise if the sun is below the horizon."""
    if pos.elevation_deg < 0:
        raise ValueError(f"the sun is below the horizon ({pos.elevation_deg:.2f} deg)")
    return round(pos.htrdr_azimuth_deg, 4), round(min(pos.elevation_deg, 90.0), 4)
