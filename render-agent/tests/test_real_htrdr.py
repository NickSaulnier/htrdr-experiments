"""Against the real htrdr-atmosphere. Skipped unless it and the gas data are installed
(scripts/build-htrdr.sh, scripts/fetch-data.sh)."""

import os
from pathlib import Path

import pytest

from htrdr_agent.argv import build_argv
from htrdr_agent.image import load
from htrdr_agent.runner import HtrdrInstall, run_sync
from htrdr_agent.scene import Inputs, PerspectiveCamera, SceneSpec, Spectral
from htrdr_agent.stats import compute_stats

INSTALL = HtrdrInstall()
GAS = Path(os.environ.get("HTRDR_DATA", Path.home() / "htrdr-data")) / "etc" / "ecrad_opt_prop.txt"

pytestmark = [
    pytest.mark.htrdr,
    pytest.mark.skipif(not (INSTALL.available() and GAS.is_file()), reason="htrdr or its gas data isn't installed"),
]


def scene(spectral: Spectral) -> SceneSpec:
    return SceneSpec(
        inputs=Inputs(atmosphere=GAS),
        spectral=spectral,
        sensor=PerspectiveCamera(pos=(0, 0, 2000), tgt=(1000, 0, 2000), fov_deg=60),
        image={"width": 32, "height": 24},
    )


def render(spec: SceneSpec, spp: int, tmp_path: Path):
    out = tmp_path / f"r{spp}.txt"
    argv = build_argv(spec, spp=spp, output=out, binary=INSTALL.binary, cache=tmp_path / f"{spec.cache_key()}.cache")
    result = run_sync(argv, timeout_s=300, env=INSTALL.env())
    assert result.ok, "\n".join(result.stderr_tail)
    return compute_stats(load(out), spp=spp, spectral=spec.spectral)


def test_monochromatic_10um_recomputes_brightness_temperature(tmp_path: Path):
    s = render(scene(Spectral(mode="lw", range_nm=(10000, 10000))), 16, tmp_path)
    assert s.layout == "longwave"
    # Measured in M0: htrdr reports ~53 K; the radiance says ~229 K.
    assert 200 < s.quantities[0].mean < 260
    assert any("disagrees with its radiance" in n for n in s.notes)
    assert s.cost.mean_time_per_path_us > 0


def test_error_falls_with_spp_as_predicted(tmp_path: Path):
    spec = scene(Spectral(mode="lw", range_nm=(9500, 10500)))
    low, high = render(spec, 8, tmp_path), render(spec, 128, tmp_path)
    ratio = low.error.percentiles["p50"] / high.error.percentiles["p50"]
    assert ratio == pytest.approx(4, rel=0.25)  # √(128/8)
    assert not any("disagrees" in n for n in high.notes)
