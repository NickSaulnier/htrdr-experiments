import sys
from pathlib import Path

import pytest

from htrdr_agent.scene import Inputs, PerspectiveCamera, SceneSpec, Spectral, Sun

# The browser viewer's fixtures: the same htrdr-image format, already checked
# against htpp.
VIEWER_FIXTURES = Path(__file__).resolve().parents[2] / "fixtures"

FAKE_HTRDR = [sys.executable, "-m", "htrdr_agent.fake"]


@pytest.fixture
def gas(tmp_path: Path) -> Path:
    """A stand-in gas file: the fake only checks that -a exists."""
    path = tmp_path / "gas.txt"
    path.write_text("fake gas optical properties\n")
    return path


@pytest.fixture
def ir_scene(gas: Path) -> SceneSpec:
    """The plan's example: IR at 10 µm, sun at 40°, from 2 km, looking at the horizon."""
    return SceneSpec(
        inputs=Inputs(atmosphere=gas),
        spectral=Spectral(mode="lw", range_nm=(10000, 10000)),
        sun=Sun(azimuth_deg=180, elevation_deg=40),
        sensor=PerspectiveCamera(pos=(0, 0, 2000), tgt=(1000, 0, 2000), fov_deg=60),
        image={"width": 64, "height": 48},
    )
