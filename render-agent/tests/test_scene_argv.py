from pathlib import Path

import pytest
from pydantic import ValidationError

from htrdr_agent.argv import build_argv, command_line, num
from htrdr_agent.scene import FluxSensor, Inputs, OrthographicCamera, PerspectiveCamera, SceneSpec, Spectral, Sun


def test_example_request_command_line(ir_scene: SceneSpec):
    argv = build_argv(ir_scene, spp=16, output="out.txt", binary="htrdr-atmosphere", cache="sky.cache", threads=8)
    assert argv == [
        "htrdr-atmosphere",
        "-a", str(ir_scene.inputs.atmosphere),
        "-s", "lw=10000,10000",
        "-D", "180,40",
        "-C", "pos=0,0,2000:tgt=1000,0,2000:up=0,0,1:fov=60",
        "-i", "def=64x48:spp=16",
        "-O", "sky.cache",
        "-t", "8",
        "-f", "-o", "out.txt",
    ]


def test_every_input_and_sensor_kind(tmp_path: Path):
    files = {k: tmp_path / f"{k}.dat" for k in ("gas", "clouds", "mie", "ground", "mtl")}
    inputs = Inputs(
        atmosphere=files["gas"], clouds=files["clouds"], mie=files["mie"], ground=files["ground"],
        materials=files["mtl"], repeat_clouds=True, repeat_ground=True, sky_material="air",
    )
    ortho = SceneSpec(
        inputs=inputs,
        spectral=Spectral(mode="sw", range_nm=(380, 780), tref_k=5778),
        sensor=OrthographicCamera(pos=(0, 0, 10), tgt=(0, 0, 0), up=(0, 1, 0), height_m=2.5),
    )
    argv = build_argv(ortho, spp=4, output="o.txt", binary="h")
    assert argv[:14] == [
        "h", "-a", str(files["gas"]), "-c", str(files["clouds"]), "-m", str(files["mie"]),
        "-g", str(files["ground"]), "-M", str(files["mtl"]), "-R", "-r", "-n",
    ]
    assert "sw=380,780:Tref=5778" in argv
    assert argv[argv.index("-P") + 1] == "pos=0,0,10:tgt=0,0,0:up=0,1,0:height=2.5"

    flux = ortho.model_copy(update={"sensor": FluxSensor(pos=(0, 0, 1), tgt=(0, 0, 2), size_m=(2, 3))})
    assert build_argv(flux, spp=1, output="o", binary="h")[-7:-3] == ["-p", "pos=0,0,1:tgt=0,0,2:up=0,1,0:sz=2,3", "-i", "def=320x240:spp=1"]


def test_command_line_is_shell_safe(gas: Path):
    spec = SceneSpec(inputs=Inputs(atmosphere=Path("/data/my gas.txt")), sensor=PerspectiveCamera(pos=(0, 0, 1), tgt=(1, 0, 1)))
    line = command_line(build_argv(spec, spp=1, output="out file.txt"))
    assert "'/data/my gas.txt'" in line and "'out file.txt'" in line


@pytest.mark.parametrize(("value", "text"), [(2000, "2000"), (2000.0, "2000"), (0.1 + 0.2, "0.3"), (-1.5, "-1.5"), (1e-7, "1e-07")])
def test_numbers_are_short(value, text):
    assert num(value) == text


def test_rejects_what_htrdr_would(gas: Path):
    with pytest.raises(ValidationError, match="needs a wavelength range"):
        Spectral(mode="lw")
    with pytest.raises(ValidationError, match="invalid wavelength range"):
        Spectral(mode="sw", range_nm=(800, 400))
    with pytest.raises(ValidationError, match="takes no wavelength range"):
        Spectral(mode="cie_xyz", range_nm=(400, 700))
    with pytest.raises(ValidationError):
        Sun(azimuth_deg=360, elevation_deg=10)
    with pytest.raises(ValidationError):
        Sun(azimuth_deg=0, elevation_deg=91)
    with pytest.raises(ValidationError, match="coincide"):
        PerspectiveCamera(pos=(1, 2, 3), tgt=(1, 2, 3))
    with pytest.raises(ValidationError, match="parallel"):
        PerspectiveCamera(pos=(0, 0, 0), tgt=(0, 0, 10))
    with pytest.raises(ValidationError, match="need droplet"):
        Inputs(atmosphere=gas, clouds=gas)
    with pytest.raises(ValueError, match="spp"):
        build_argv(SceneSpec(inputs=Inputs(atmosphere=gas), sensor=PerspectiveCamera(pos=(0, 0, 1), tgt=(1, 0, 1))), spp=0, output="o")


def test_warnings_flag_the_sun_in_longwave_and_the_tb_bug(ir_scene: SceneSpec):
    w = " ".join(ir_scene.warnings())
    assert "no effect in lw" in w
    assert "monochromatic" in w
    band = ir_scene.model_copy(update={"spectral": Spectral(mode="lw", range_nm=(9500, 10500)), "sun": None})
    assert band.warnings() == []


def test_warnings_for_missing_sun_and_downward_view_without_ground(gas: Path):
    spec = SceneSpec(inputs=Inputs(atmosphere=gas), sensor=PerspectiveCamera(pos=(0, 0, 2000), tgt=(100, 0, 0)))
    w = " ".join(spec.warnings())
    assert "No sun direction" in w and "no ground" in w


def test_cache_key_ignores_camera_sun_and_image(ir_scene: SceneSpec):
    moved = ir_scene.model_copy(update={
        "sensor": PerspectiveCamera(pos=(5, 5, 100), tgt=(0, 0, 0)), "sun": None, "image": {"width": 8, "height": 8},
    })
    assert moved.cache_key() == ir_scene.cache_key()
    other_band = ir_scene.model_copy(update={"spectral": Spectral(mode="lw", range_nm=(8000, 8000))})
    assert other_band.cache_key() != ir_scene.cache_key()


def test_spec_round_trips_through_json(ir_scene: SceneSpec):
    assert SceneSpec.model_validate_json(ir_scene.model_dump_json()) == ir_scene
