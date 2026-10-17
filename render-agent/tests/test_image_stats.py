import math

import numpy as np
import pytest

from htrdr_agent.estimate import CostModel, propose, spp_for_target
from htrdr_agent.image import (
    HtrdrImageError,
    brightness_temperature,
    brightness_temperature_sigma,
    load,
    parse,
    planck_nm,
)
from htrdr_agent.scene import Spectral
from htrdr_agent.stats import compute_stats

from .conftest import VIEWER_FIXTURES


# --- parsing --------------------------------------------------------------


def test_parses_row_major_from_the_top_left():
    img = parse("2 1\n1 2 3 4 5 6 7 8\n9 10 11 12 13 14 15 16\n")
    assert (img.width, img.height) == (2, 1)
    assert img.data.shape == (1, 2, 8)
    assert img.data[0, 1, 0] == 9
    assert img.mean(1)[0, 0] == 3 and img.sigma(3)[0, 1] == 16


def test_comments_blank_lines_crlf_and_six_value_lines():
    img = parse("# header\r\n\r\n2 1 # def\r\n1 2 3 4 5 6 # short\r\n  # only a comment\r\n1 2 3 4 5 6 7 8\r\n")
    assert img.data[0, 0].tolist() == [1, 2, 3, 4, 5, 6, 0, 0]
    assert img.data[0, 1].tolist() == [1, 2, 3, 4, 5, 6, 7, 8]


def test_printf_spellings_of_nan_and_infinity():
    img = parse("1 1\nnan -nan inf -inf 1 1 1 1\n")
    v = img.data[0, 0]
    assert math.isnan(v[0]) and math.isnan(v[1]) and v[2] == math.inf and v[3] == -math.inf


@pytest.mark.parametrize(
    ("text", "message"),
    [
        ("", "missing image definition"),
        ("3\n", 'line 1: expected the image definition'),
        ("0 2\n", "invalid image definition 0x2"),
        ("1 2\n1 2 3 4 5 6 7 8\n1 2 3 x 5 6 7 8\n", 'line 3: invalid number "x"'),
        ("1 1\n1 2 3 4 5\n", "line 2: expected 6 to 8 values per pixel, found 5"),
        ("2 1\n1 2 3 4 5 6 7 8\n", "truncated image: expected 2 pixels, found 1"),
        ("1 1\n1 2 3 4 5 6 7 8\n1 2 3 4 5 6 7 8\n", "line 3: more pixels"),
    ],
)
def test_malformed_files_name_the_line(text, message):
    with pytest.raises(HtrdrImageError, match=message):
        parse(text)


@pytest.mark.parametrize(("name", "layout"), [("xyz.txt", "xyz"), ("longwave.txt", "longwave"), ("shortwave.txt", "shortwave")])
def test_reads_the_viewer_fixtures(name, layout):
    img = load(VIEWER_FIXTURES / name)
    assert (img.width, img.height) == (64, 48)
    assert img.layout() == layout


# --- Planck ---------------------------------------------------------------


def test_brightness_temperature_inverts_planck():
    t = np.array([53.3, 229.4, 290.0])
    assert np.allclose(brightness_temperature(10000, planck_nm(10000, t)), t)
    # The value measured from the real htrdr smoke render.
    assert brightness_temperature(10000, np.array([0.002256]))[0] == pytest.approx(229.4, abs=0.1)
    assert math.isnan(brightness_temperature(10000, np.array([0.0]))[0])


def test_brightness_temperature_sigma_matches_finite_differences():
    t, dt = 250.0, 1e-3
    db_dt = (planck_nm(10000, t + dt) - planck_nm(10000, t - dt)) / (2 * dt)
    sigma_l = 1e-4
    assert brightness_temperature_sigma(10000, np.array([t]), np.array([sigma_l]))[0] == pytest.approx(sigma_l / db_dt, rel=1e-6)


# --- stats ----------------------------------------------------------------


def test_stats_on_the_xyz_fixture():
    img = load(VIEWER_FIXTURES / "xyz.txt")
    s = compute_stats(img, spp=64, target_rel_err=0.3)
    assert s.layout == "xyz" and s.error.quantity == "Y"
    assert [q.name for q in s.quantities] == ["X", "Y", "Z"]
    rel = img.sigma(1) / np.abs(img.mean(1))
    assert s.error.percentiles["p95"] == pytest.approx(np.percentile(rel, 95))
    assert s.error.fraction_meeting_target == pytest.approx(np.mean(rel <= 0.3))
    assert s.error.pixels == 64 * 48
    assert len(s.error.worst_tiles) == 5
    assert s.error.worst_tiles[0]["p95"] >= s.error.worst_tiles[-1]["p95"]
    assert s.cost.paths == 64 * 48 * 64 * 3
    assert s.cost.cpu_s == pytest.approx(img.mean(3).sum() * 64 * 3 * 1e-6)
    assert s.nonfinite_pixels == 0


def test_stats_flag_and_fix_htrdrs_monochromatic_tb():
    # Radiance for 229.4 K at 10 µm, with htrdr 0.12's wrong Tb beside it.
    radiance = float(planck_nm(10000, 229.4))
    line = f"53.3 1 {radiance} {radiance * 0.02} 0 0 300 30\n"
    img = parse("2 1\n" + line * 2)
    s = compute_stats(img, spp=16, spectral=Spectral(mode="lw", range_nm=(10000, 10000)))
    tb = s.quantities[0]
    assert tb.name == "brightness temperature" and tb.mean == pytest.approx(229.4, abs=0.01)
    assert any("disagrees with its radiance" in n for n in s.notes)
    # A band run trusts htrdr's Tb.
    band = compute_stats(img, spp=16, spectral=Spectral(mode="lw", range_nm=(9500, 10500)))
    assert band.quantities[0].mean == pytest.approx(53.3)
    assert not any("disagrees" in n for n in band.notes)


def test_stats_with_no_usable_pixels_and_nonfinite_values():
    s = compute_stats(parse("2 1\n0 0 0 0 0 0 1 0\nnan 0 0 0 0 0 1 0\n"), spp=1, spectral=Spectral())
    assert math.isnan(s.error.percentiles["p95"]) and s.error.pixels == 0
    assert s.nonfinite_pixels == 1
    assert any("can't be estimated" in n for n in s.notes)


def test_stats_serialize_to_plain_data():
    s = compute_stats(load(VIEWER_FIXTURES / "longwave.txt"), spp=8, target_rel_err=0.01)
    d = s.to_dict()
    assert d["error"]["quantity"] == "radiance" and isinstance(d["quantities"][0]["mean"], float)


# --- estimates ------------------------------------------------------------


def test_spp_scales_with_the_square_of_the_error_ratio():
    assert spp_for_target(32, 0.41, 0.01, safety=1.0) == math.ceil(32 * 41**2)
    assert spp_for_target(16, 0.04, 0.01, safety=1.2) == math.ceil(16 * 16 * 1.2)
    assert spp_for_target(16, 0.005, 0.01) == 16
    assert spp_for_target(16, 0.0100001, 0.01, safety=1.0) == 17  # always at least one more
    with pytest.raises(ValueError):
        spp_for_target(16, math.nan, 0.01)


def test_cost_model_fits_fixed_and_per_spp_time():
    m = CostModel.fit([(16, 1.0 + 16 * 0.5), (64, 1.0 + 64 * 0.5), (256, 1.0 + 256 * 0.5)])
    assert m.fixed_s == pytest.approx(1.0) and m.per_spp_s == pytest.approx(0.5)
    one = CostModel.fit([(32, 4.0)])
    assert (one.fixed_s, one.per_spp_s) == (0.0, 0.125)
    assert one.predict(64) == 8.0


def test_proposal_respects_target_cap_and_budget():
    s = compute_stats(load(VIEWER_FIXTURES / "xyz.txt"), spp=64)
    p95 = s.error.percentiles["p95"]
    cost = CostModel(0.0, 0.01)
    p = propose(s, target=p95 / 2, cost=cost, safety=1.0)
    assert p.spp == math.ceil(64 * 4) and not p.target_met and not p.capped
    assert p.predicted_wall_s == pytest.approx(p.spp * 0.01)
    capped = propose(s, target=p95 / 10, cost=cost, max_spp=1000, budget_s=5)
    assert capped.capped and capped.spp == 1000 and not capped.within_budget
    met = propose(s, target=p95 * 2, cost=cost)
    assert met.target_met and met.spp == 64
    assert not p.stepped and not met.stepped


def test_proposal_grows_spp_in_bounded_steps():
    s = compute_stats(load(VIEWER_FIXTURES / "xyz.txt"), spp=64)
    far = propose(s, target=s.error.percentiles["p95"] / 100, cost=CostModel(0.0, 0.01))
    assert far.stepped and far.spp == 64 * 16 and not far.target_met
    unbounded = propose(s, target=s.error.percentiles["p95"] / 100, cost=CostModel(0.0, 0.01), max_growth=None)
    assert not unbounded.stepped and unbounded.spp > 64 * 16
