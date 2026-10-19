from datetime import datetime, timedelta, timezone

import pytest

from citylapse.sun import htrdr_sun, julian_day, solar_position, to_htrdr_azimuth

TOULOUSE = (43.6, 1.44)
CEST = timezone(timedelta(hours=2))


def test_julian_day_of_j2000():
    assert julian_day(datetime(2000, 1, 1, 12, tzinfo=timezone.utc)) == pytest.approx(2451545.0)


def test_naive_datetimes_are_refused():
    with pytest.raises(ValueError):
        solar_position(datetime(2026, 6, 21, 12), *TOULOUSE)


def test_solstice_noon_elevation_and_declination():
    # At local solar noon on 21 June the declination is close to the
    # obliquity, and the elevation is 90 - latitude + declination.
    lat, lon = TOULOUSE
    best = max(
        (solar_position(datetime(2026, 6, 21, 11, 0, tzinfo=timezone.utc) + timedelta(minutes=m), lat, lon, refraction=False)
         for m in range(0, 120)),
        key=lambda p: p.elevation_deg,
    )
    assert best.declination_deg == pytest.approx(23.44, abs=0.02)
    assert best.elevation_deg == pytest.approx(90 - lat + best.declination_deg, abs=0.01)
    assert best.azimuth_deg == pytest.approx(180, abs=0.5)


@pytest.mark.parametrize("when", [
    datetime(2026, 6, 21, 6, 30, tzinfo=CEST),
    datetime(2026, 6, 21, 9, 0, tzinfo=CEST),
    datetime(2026, 6, 21, 13, 45, tzinfo=CEST),
    datetime(2026, 6, 21, 18, 15, tzinfo=CEST),
    datetime(2026, 6, 21, 21, 30, tzinfo=CEST),
    datetime(2026, 12, 21, 12, 0, tzinfo=timezone.utc),
    datetime(2026, 3, 20, 7, 10, tzinfo=timezone.utc),
])
def test_matches_pvlib_spa(when):
    # NREL's SPA (via pvlib) is an independent, more precise algorithm.
    pd = pytest.importorskip("pandas")
    pvlib = pytest.importorskip("pvlib")
    lat, lon = TOULOUSE
    ref = pvlib.solarposition.spa_python(pd.DatetimeIndex([when]), lat, lon, altitude=150)
    ours = solar_position(when, lat, lon, refraction=False)
    assert ours.elevation_deg == pytest.approx(90 - float(ref["zenith"].iloc[0]), abs=0.02)
    assert ours.azimuth_deg == pytest.approx(float(ref["azimuth"].iloc[0]), abs=0.05)
    if ours.elevation_deg > 2:
        # Refraction lifts the sun by a fraction of a degree near the horizon.
        app = solar_position(when, lat, lon)
        assert app.elevation_deg == pytest.approx(float(ref["apparent_elevation"].iloc[0]), abs=0.05)


def test_sun_rises_in_the_east_and_sets_in_the_west():
    lat, lon = TOULOUSE
    morning = solar_position(datetime(2026, 6, 21, 7, 0, tzinfo=CEST), lat, lon)
    evening = solar_position(datetime(2026, 6, 21, 21, 0, tzinfo=CEST), lat, lon)
    assert 45 < morning.azimuth_deg < 90  # north-east of east in June
    assert 270 < evening.azimuth_deg < 315


@pytest.mark.parametrize("compass,expected", [(0, 90), (90, 0), (180, 270), (270, 180), (45, 45), (135, 315)])
def test_htrdr_azimuth(compass, expected):
    # htrdr: counter-clockwise from +X (east). North (+Y) is 90.
    assert to_htrdr_azimuth(compass) == pytest.approx(expected)


def test_htrdr_sun_refuses_night():
    lat, lon = TOULOUSE
    with pytest.raises(ValueError):
        htrdr_sun(solar_position(datetime(2026, 6, 21, 2, 0, tzinfo=CEST), lat, lon))
    az, el = htrdr_sun(solar_position(datetime(2026, 6, 21, 14, 0, tzinfo=CEST), lat, lon))
    assert 0 <= az < 360 and 60 < el <= 90
