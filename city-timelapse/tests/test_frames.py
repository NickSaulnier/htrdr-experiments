import json
import sys
from pathlib import Path

import numpy as np
import pytest
from htrdr_agent.image import HtrdrImage

from citylapse.city import CityConfig, generate, write_materials, write_obj
from citylapse.frames import (DayConfig, RunConfig, ViewConfig, build_jobs, calibrate, materials_for, noise,
                              run_jobs, status)
from citylapse.planck import band_radiance, band_tb_sigma
from citylapse.sun import solar_position


@pytest.fixture
def fake_prefix(tmp_path, monkeypatch):
    """An htrdr install whose binary is render-agent's fake htrdr."""
    prefix = tmp_path / "prefix"
    (prefix / "bin").mkdir(parents=True)
    (prefix / "lib").mkdir()
    exe = prefix / "bin" / "htrdr-atmosphere"
    exe.write_text(f'#!/bin/sh\nexec "{sys.executable}" -m htrdr_agent.fake "$@"\n')
    exe.chmod(0o755)
    monkeypatch.setenv("HTRDR_PREFIX", str(prefix))
    for knob in ("FAKE_HTRDR_FAIL", "FAKE_HTRDR_SIGFPE_ABOVE_THREADS", "FAKE_HTRDR_SLEEP"):
        monkeypatch.delenv(knob, raising=False)
    return prefix


@pytest.fixture
def cfg(tmp_path):
    city = tmp_path / "city"
    city.mkdir()
    c = generate(CityConfig(seed=3, blocks=1))
    write_obj(c, city / "city.obj")
    write_materials(c, Path("/spk"), city / "city.mtls")
    gas = tmp_path / "gas.txt"
    gas.write_text("fake gas optical properties\n")
    return RunConfig(city=str(city), gas=str(gas),
                     day=DayConfig(start="05:00", end="21:00", step_min=240),  # 05, 09, 13, 17, 21
                     view=ViewConfig(width=32, height=18), spp={"visible": 16, "thermal": 16}, threads=8)


def test_schedule_has_thermal_always_and_visible_in_daylight(cfg):
    jobs = build_jobs(cfg)
    thermal = [j for j in jobs if j.kind == "thermal"]
    visible = [j for j in jobs if j.kind == "visible"]
    assert [f"{j.time:%H%M}" for j in thermal] == ["0500", "0900", "1300", "1700", "2100"]
    # At 05:00 CEST on 21 June in Toulouse the sun is still below the horizon.
    assert [f"{j.time:%H%M}" for j in visible] == ["0900", "1300", "1700", "2100"]
    assert len({j.id for j in jobs}) == len(jobs)
    noon = next(j for j in visible if j.time.hour == 13)
    pos = solar_position(noon.time, cfg.day.latitude, cfg.day.longitude)
    assert noon.spec.sun.elevation_deg == pytest.approx(pos.elevation_deg, abs=1e-3)
    assert noon.spec.sun.azimuth_deg == pytest.approx((90 - pos.azimuth_deg) % 360, abs=1e-3)
    assert all(j.spec.inputs.repeat_ground for j in jobs)
    assert all(j.spec.sun is None for j in thermal)


def test_per_step_materials_are_used_when_present(cfg):
    city = Path(cfg.city)
    (city / "materials").mkdir()
    (city / "materials" / "1300.mtls").write_text("air none\n")
    jobs = {j.id: j for j in build_jobs(cfg)}
    assert jobs["1300-thermal"].spec.inputs.materials == city / "materials" / "1300.mtls"
    assert jobs["0900-thermal"].spec.inputs.materials == city / "city.mtls"


def test_run_renders_every_frame_then_resumes_without_rerendering(cfg, tmp_path, fake_prefix):
    run = tmp_path / "run"
    jobs = build_jobs(cfg)
    first = run_jobs(run, cfg, jobs, limit=3, log=lambda *_: None)
    assert first == {"done": 0, "rendered": 3, "failed": 0}
    second = run_jobs(run, cfg, jobs, log=lambda *_: None)
    assert second == {"done": 3, "rendered": len(jobs) - 3, "failed": 0}
    third = run_jobs(run, cfg, jobs, log=lambda *_: None)
    assert third == {"done": len(jobs), "rendered": 0, "failed": 0}
    st = status(run, cfg)
    assert st["done"] == len(jobs) and st["pending"] == 0

    rec = json.loads((run / "jobs" / "1300-visible" / "job.json").read_text())
    assert rec["status"] == "ok" and rec["spp"] == 16 and len(rec["attempts"]) == 1
    assert rec["argv"][-1].endswith("1300-visible/frame.txt")
    assert "-D" in rec["argv"] and rec["noise"]["value"] > 0
    assert set(rec["inputs"]) == {cfg.gas, str(Path(cfg.city) / "city.obj"), str(Path(cfg.city) / "city.mtls")}


def test_changed_inputs_or_spp_rerender_only_what_they_touch(cfg, tmp_path, fake_prefix):
    run = tmp_path / "run"
    jobs = build_jobs(cfg)
    run_jobs(run, cfg, jobs, log=lambda *_: None)
    cfg.spp["thermal"] = 32
    again = run_jobs(run, cfg, build_jobs(cfg), log=lambda *_: None)
    assert again == {"done": 4, "rendered": 5, "failed": 0}
    # Editing the materials file changes every job's key.
    mtls = Path(cfg.city) / "city.mtls"
    mtls.write_text(mtls.read_text() + "# edited\n")
    assert run_jobs(run, cfg, build_jobs(cfg), log=lambda *_: None)["rendered"] == len(jobs)


def test_a_killed_render_leaves_no_frame_and_is_redone(cfg, tmp_path, fake_prefix):
    run = tmp_path / "run"
    jobs = build_jobs(cfg)
    jd = run / "jobs" / jobs[0].id
    jd.mkdir(parents=True)
    (jd / "frame.txt.partial").write_text("half a frame")
    counts = run_jobs(run, cfg, jobs[:1], log=lambda *_: None)
    assert counts["rendered"] == 1
    assert not (jd / "frame.txt.partial").exists()
    assert (jd / "frame.txt").stat().st_size > 0


def test_sigfpe_is_retried_with_fewer_threads(cfg, tmp_path, fake_prefix, monkeypatch):
    monkeypatch.setenv("FAKE_HTRDR_SIGFPE_ABOVE_THREADS", "2")
    run = tmp_path / "run"
    job = build_jobs(cfg)[0]
    assert run_jobs(run, cfg, [job], log=lambda *_: None)["rendered"] == 1
    rec = json.loads((run / "jobs" / job.id / "job.json").read_text())
    assert [a["threads"] for a in rec["attempts"]] == [8, 4, 2]
    assert [a["returncode"] for a in rec["attempts"]][:2] == [-8, -8]


def test_other_failures_are_not_retried(cfg, tmp_path, fake_prefix, monkeypatch):
    monkeypatch.setenv("FAKE_HTRDR_FAIL", "1")
    run = tmp_path / "run"
    job = build_jobs(cfg)[0]
    assert run_jobs(run, cfg, [job], log=lambda *_: None)["failed"] == 1
    rec = json.loads((run / "jobs" / job.id / "job.json").read_text())
    assert rec["status"] == "failed" and len(rec["attempts"]) == 1
    assert not (run / "jobs" / job.id / "frame.txt").exists()
    # A failed job is tried again on the next run.
    monkeypatch.delenv("FAKE_HTRDR_FAIL")
    assert run_jobs(run, cfg, [job], log=lambda *_: None)["rendered"] == 1


def test_thermal_noise_uses_the_radiance_sigma():
    t = np.full((2, 3), 300.0)
    radiance = band_radiance(t, 9000, 10000)
    data = np.zeros((2, 3, 8))
    data[:, :, 0] = t
    data[:, :, 1] = 99.0  # htrdr's own sigma(Tb): ignored
    data[:, :, 2] = radiance
    data[:, :, 3] = 0.02 * radiance
    n = noise("thermal", HtrdrImage(3, 2, data))
    assert n["value"] == pytest.approx(float(band_tb_sigma(300.0, 0.02 * radiance[0, 0], 9000, 10000)), rel=1e-6)
    assert n["tb_median"] == pytest.approx(300.0, abs=1e-6)


def test_visible_noise_is_relative_to_the_frame_white():
    y = np.tile(np.linspace(0.0, 10.0, 50), (20, 1))  # a smooth ramp: box filtering keeps its top
    data = np.zeros((20, 50, 8))
    data[:, :, 2] = y
    sigma = np.full((20, 50), 0.1)  # the same sigma almost everywhere, so dark pixels have a large relative error
    sigma[0, :5] = 1.0  # a few heavy pixels: the RMS sees them, the median doesn't
    data[:, :, 3] = sigma
    n = noise("visible", HtrdrImage(50, 20, data))
    assert n["white_y"] == pytest.approx(np.percentile(y, 99.5), rel=0.01)
    assert n["value"] == pytest.approx(np.sqrt(np.mean(sigma**2)) / n["white_y"])
    assert n["median"] == pytest.approx(0.1 / n["white_y"])
    assert n["pixel_relative_median"] > n["value"]


def test_noise_ignores_a_single_hot_pixel_when_finding_the_white():
    data = np.zeros((20, 50, 8))
    data[:, :, 2] = 1.0
    data[5, 5, 2] = 500.0  # one firefly
    data[:, :, 3] = 0.01
    n = noise("visible", HtrdrImage(50, 20, data))
    assert n["white_y"] < 60  # one 9th of the firefly at most, not 500


def test_calibration_suggests_more_spp_for_a_tighter_target(cfg, tmp_path, fake_prefix):
    cfg.targets = {"visible": 0.05, "thermal": 0.05}
    out = calibrate(tmp_path / "run", cfg, preview_spp=16, log=lambda *_: None)
    for kind in ("visible", "thermal"):
        assert out[kind]["suggested_spp"] > 16
        assert out[kind]["predicted_total_h"] >= 0
        assert len(out[kind]["probes"]) >= 2
