import asyncio
import os
from pathlib import Path

import numpy as np
import pytest

from htrdr_agent.argv import build_argv
from htrdr_agent.estimate import spp_for_target
from htrdr_agent.image import load
from htrdr_agent.runner import HtrdrInstall, run, run_sync
from htrdr_agent.scene import Inputs, PerspectiveCamera, SceneSpec, Spectral, Sun
from htrdr_agent.stats import compute_stats

from .conftest import FAKE_HTRDR


def fake_argv(spec: SceneSpec, spp: int, out: Path, **kw) -> list[str]:
    argv = build_argv(spec, spp=spp, output=out, binary="htrdr-atmosphere", **kw)
    return FAKE_HTRDR + argv[1:]


def env(**extra: str) -> dict[str, str]:
    return {**os.environ, **extra}


def test_fake_render_round_trips_through_parser_and_stats(ir_scene: SceneSpec, tmp_path: Path):
    out = tmp_path / "r.txt"
    result = run_sync(fake_argv(ir_scene, 16, out, cache=tmp_path / "c"), timeout_s=60)
    assert result.ok, result.stderr_tail
    assert result.command_line.endswith(f"-f -o {out}")
    img = load(out)
    assert (img.width, img.height) == (64, 48)
    s = compute_stats(img, spp=16, spectral=ir_scene.spectral)
    # The fake reproduces htrdr 0.12's monochromatic Tb bug; stats see through it.
    assert any("disagrees with its radiance" in n for n in s.notes)
    assert 200 < s.quantities[0].mean < 290
    assert 0.015 < s.error.percentiles["p50"] < 0.025  # ≈ 0.08/√16
    assert (tmp_path / "c").exists()


def test_fake_error_falls_as_one_over_root_spp(gas: Path, tmp_path: Path):
    spec = SceneSpec(
        inputs={"atmosphere": gas}, spectral=Spectral(), sun=Sun(azimuth_deg=0, elevation_deg=40),
        sensor=PerspectiveCamera(pos=(0, 0, 2000), tgt=(1000, 0, 2300)), image={"width": 80, "height": 60},
    )
    p50, p95 = {}, {}
    for spp in (8, 32, 128, 512):
        run_sync(fake_argv(spec, spp, tmp_path / f"{spp}.txt"), timeout_s=60)
        e = compute_stats(load(tmp_path / f"{spp}.txt"), spp=spp, spectral=spec.spectral).error.percentiles
        p50[spp], p95[spp] = e["p50"], e["p95"]
    # The median relative error follows 1/√spp closely once the per-pixel
    # error is moderate; at ~47 % (8 spp) even it drifts a few percent.
    assert p50[8] / p50[32] == pytest.approx(2, rel=0.1)
    for lo, hi in ((32, 128), (128, 512)):
        assert p50[lo] / p50[hi] == pytest.approx(2, rel=0.05)
    # p95 of σ/|μ̂| is biased high by the noisy mean, less so as spp grows...
    ratios = [p95[lo] / p95[hi] for lo, hi in ((8, 32), (32, 128), (128, 512))]
    assert ratios[0] > ratios[1] > ratios[2] > 2
    # ...so a p95-based estimate asks for too many samples, never too few.
    assert spp_for_target(32, p95[32], p95[512], safety=1.0) > 512
    assert spp_for_target(32, p50[32], p50[512], safety=1.0) == pytest.approx(512, rel=0.1)


def test_fake_is_deterministic_and_needs_force_to_overwrite(ir_scene: SceneSpec, tmp_path: Path):
    a, b = tmp_path / "a.txt", tmp_path / "b.txt"
    run_sync(fake_argv(ir_scene, 4, a), timeout_s=60)
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    (elsewhere / "gas.txt").write_text("x")
    moved = ir_scene.model_copy(update={"inputs": Inputs(atmosphere=elsewhere / "gas.txt")})
    run_sync(fake_argv(moved, 4, b, cache=elsewhere / "c"), timeout_s=60)
    # Same scene, different file locations: same pixels.
    assert np.array_equal(load(a).data, load(b).data)
    # Different spp: different noise.
    run_sync(fake_argv(ir_scene, 8, b), timeout_s=60)
    assert not np.array_equal(load(a).data, load(b).data)
    argv = fake_argv(ir_scene, 4, a)
    argv.remove("-f")
    r = run_sync(argv, timeout_s=60)
    assert r.status == "failed" and "use -f" in r.stderr_tail[-1]


def test_failure_is_reported_with_stderr(ir_scene: SceneSpec, tmp_path: Path):
    r = run_sync(fake_argv(ir_scene, 1, tmp_path / "x.txt"), timeout_s=60, env=env(FAKE_HTRDR_FAIL="1"))
    assert r.status == "failed" and r.returncode == 1
    assert "simulated failure" in r.stderr_tail[-1]
    missing = fake_argv(ir_scene, 1, tmp_path / "y.txt")
    missing[missing.index("-a") + 1] = str(tmp_path / "nope")
    assert "cannot open" in run_sync(missing, timeout_s=60).stderr_tail[-1]


def test_timeout_stops_the_process(ir_scene: SceneSpec, tmp_path: Path):
    r = run_sync(fake_argv(ir_scene, 1, tmp_path / "t.txt"), timeout_s=0.5, env=env(FAKE_HTRDR_SLEEP="30"))
    assert r.status == "timeout"
    assert r.returncode is not None and r.returncode < 0  # killed by a signal
    assert r.wall_s < 10
    assert not (tmp_path / "t.txt").exists()


def test_cancel_stops_the_process(ir_scene: SceneSpec, tmp_path: Path):
    async def scenario():
        cancel = asyncio.Event()
        task = asyncio.create_task(run(fake_argv(ir_scene, 1, tmp_path / "c.txt"), env=env(FAKE_HTRDR_SLEEP="30"), cancel=cancel))
        await asyncio.sleep(0.5)
        cancel.set()
        return await task

    r = asyncio.run(scenario())
    assert r.status == "cancelled" and r.wall_s < 10


def test_output_lines_reach_the_callback(tmp_path: Path):
    seen: list[str] = []
    script = "import sys; print('step 1'); sys.stderr.write('progress 50%\\rprogress 100%\\n'); print('done')"
    r = run_sync([os.sys.executable, "-c", script], timeout_s=30, on_line=seen.append)
    assert r.ok
    assert seen.count("step 1") == 1 and "progress 50%" in seen and "progress 100%" in seen
    assert r.stdout_tail == ["step 1", "done"]


def test_install_paths_and_env(tmp_path: Path):
    inst = HtrdrInstall(prefix=tmp_path)
    assert inst.binary == tmp_path / "bin" / "htrdr-atmosphere"
    assert inst.env()["LD_LIBRARY_PATH"].split(os.pathsep)[0] == str(tmp_path / "lib")
    assert not inst.available()
