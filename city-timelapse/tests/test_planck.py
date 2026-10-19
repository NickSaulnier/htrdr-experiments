import numpy as np
import pytest

from citylapse.planck import band_brightness_temperature, band_radiance, band_tb_sigma


def test_band_radiance_matches_a_fine_riemann_sum():
    lam = np.linspace(9000, 10000, 200001) * 1e-9
    h, c, k = 6.62607015e-34, 2.99792458e8, 1.380649e-23
    b = 2 * h * c**2 / lam**5 / np.expm1(h * c / (lam * k * 300.0)) * 1e-9
    assert band_radiance(300.0, 9000, 10000) == pytest.approx(np.trapezoid(b, lam * 1e9), rel=1e-9)


def test_inversion_round_trips():
    t = np.array([[250.0, 280.0], [300.0, 335.5]])
    back = band_brightness_temperature(band_radiance(t, 9000, 10000), 9000, 10000)
    assert np.allclose(back, t, atol=1e-6)


def test_non_positive_radiance_gives_nan():
    out = band_brightness_temperature(np.array([0.0, -1.0, 5.0]), 9000, 10000)
    assert np.isnan(out[:2]).all() and np.isfinite(out[2])


def test_sigma_is_first_order():
    t, s = 300.0, 0.05
    expected = s * 0.02 / (band_radiance(t + 0.01, 9000, 10000) - band_radiance(t - 0.01, 9000, 10000))
    assert band_tb_sigma(t, s, 9000, 10000) == pytest.approx(expected)
    # And it is consistent with perturbing the radiance directly (by a small
    # amount, so curvature stays below the tolerance).
    l = band_radiance(t, 9000, 10000)
    dt = band_brightness_temperature(l + s / 100, 9000, 10000) - t
    assert band_tb_sigma(t, s / 100, 9000, 10000) == pytest.approx(dt, rel=1e-4)
