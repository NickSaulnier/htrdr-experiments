"""The MCP server through FastMCP's in-memory client, against the fake htrdr."""

import asyncio
import json
import os
from pathlib import Path

import pytest
from fastmcp import Client
from fastmcp.exceptions import ToolError

from htrdr_agent.runner import HtrdrInstall
from htrdr_agent.server import FAKE_HTRDR, ServerConfig, create_server

HTPP = HtrdrInstall()


def config(runs: Path, sleep: str | None = "0.05", **env: str) -> ServerConfig:
    """`sleep`: the fake's fixed run time; None makes it proportional to the work instead."""
    base = {**HTPP.env(), **({"FAKE_HTRDR_SLEEP": sleep} if sleep is not None else {})}
    return ServerConfig(
        runs_dir=runs,
        htrdr=FAKE_HTRDR,
        htpp=[str(HTPP.htpp)] if HTPP.htpp.is_file() else None,
        env={**base, **env},
    )


def ir_spec(gas: Path, **over) -> dict:
    spec = {
        "inputs": {"atmosphere": str(gas)},
        "spectral": {"mode": "lw", "range_nm": [10000, 10000]},
        "sun": {"azimuth_deg": 180, "elevation_deg": 40},
        "sensor": {"kind": "perspective", "pos": [0, 0, 2000], "tgt": [1000, 0, 2000], "fov_deg": 60},
        "image": {"width": 64, "height": 48},
    }
    return {**spec, **over}


def xyz_spec(gas: Path) -> dict:
    return ir_spec(gas, spectral={"mode": "cie_xyz"}, sensor={"kind": "perspective", "pos": [0, 0, 2000], "tgt": [1000, 0, 2300]})


async def call(client: Client, tool: str, **args) -> dict:
    return (await client.call_tool(tool, args)).structured_content


async def read(client: Client, uri: str):
    return json.loads((await client.read_resource(uri))[0].text)


def test_lists_the_four_tools_and_the_resources(tmp_path: Path):
    async def scenario():
        async with Client(create_server(config(tmp_path))) as c:
            tools = {t.name for t in await c.list_tools()}
            templates = {t.uri_template for t in await c.list_resource_templates()}
            resources = {str(r.uri) for r in await c.list_resources()}
            return tools, templates, resources

    tools, templates, resources = asyncio.run(scenario())
    assert tools == {"build_scene", "render", "read_stats", "postprocess"}
    assert templates == {"htrdr://scenes/{scene_id}", "htrdr://renders/{render_id}"}
    assert resources == {"htrdr://renders"}


def test_build_scene_warns_and_is_idempotent(gas: Path, tmp_path: Path):
    async def scenario():
        async with Client(create_server(config(tmp_path / "runs"))) as c:
            a = await call(c, "build_scene", spec=ir_spec(gas))
            b = await call(c, "build_scene", spec=ir_spec(gas))
            with pytest.raises(ToolError, match="input files not found"):
                await call(c, "build_scene", spec=ir_spec(tmp_path / "missing.txt"))
            with pytest.raises(ToolError):
                await call(c, "build_scene", spec=ir_spec(gas, spectral={"mode": "lw"}))  # no range
            return a, b

    a, b = asyncio.run(scenario())
    assert a["scene_id"] == b["scene_id"]
    assert any("no effect in lw" in w for w in a["warnings"])
    assert "spp=<spp>" in a["command_template"] and "-s lw=10000,10000" in a["command_template"]


def test_preview_then_refine_until_the_target_is_met(gas: Path, tmp_path: Path):
    """The agent's numeric loop, with no LLM: preview, read_stats, render the proposal, repeat."""
    target = 0.01
    progress: list[float] = []

    async def on_progress(value: float, total: float | None, message: str | None) -> None:
        progress.append(value)

    async def scenario():
        # Run time proportional to spp, and large enough next to process-startup
        # jitter (~0.5 s) for the cost model to measure a per-spp cost.
        async with Client(create_server(config(tmp_path, sleep=None, FAKE_HTRDR_SPEED="0.1"))) as c:
            sid = (await call(c, "build_scene", spec=ir_spec(gas)))["scene_id"]
            spp, history = 4, []
            for _ in range(6):
                r = (await c.call_tool("render", {"scene_id": sid, "spp": spp}, progress_handler=on_progress)).structured_content
                assert r["status"] == "ok", r
                s = await call(c, "read_stats", render_id=r["render_id"], target_rel_err=target)
                history.append((spp, s["error"]["percentiles"]["p95"], s["proposal"]))
                if s["proposal"]["target_met"]:
                    return history, s, r
                spp = s["proposal"]["spp"]
            raise AssertionError(f"did not converge: {history}")

    history, final, last = asyncio.run(scenario())
    # 2 % at 4 spp per path noise 0.08 → ~25x the spp; the 16x step cap makes it two steps.
    assert len(history) <= 4
    assert final["error"]["percentiles"]["p95"] <= target
    assert history[0][2]["stepped"]  # the first jump was capped at 16x
    assert all(later[0] > earlier[0] for earlier, later in zip(history, history[1:]))
    # Monochromatic lw: htrdr's Tb is recomputed from the radiance.
    assert any("disagrees with its radiance" in n for n in final["notes"])
    assert 200 < final["quantities"][0]["mean"] < 290
    assert final["cost_model"]["per_spp_s"] > 0
    assert progress and progress[-1] == 100


def test_records_hold_the_exact_command_line(gas: Path, tmp_path: Path):
    async def scenario():
        async with Client(create_server(config(tmp_path))) as c:
            sid = (await call(c, "build_scene", spec=ir_spec(gas)))["scene_id"]
            r = await call(c, "render", scene_id=sid, spp=8)
            await call(c, "read_stats", render_id=r["render_id"])
            return r, await read(c, f"htrdr://renders/{r['render_id']}"), await read(c, f"htrdr://scenes/{sid}"), await read(c, "htrdr://renders")

    r, record, scene, listing = asyncio.run(scenario())
    assert record["command_line"] == r["command_line"]
    assert "-i def=64x48:spp=8" in record["command_line"] and "-v" in record["argv"]
    assert record["status"] == "ok" and record["wall_s"] > 0 and record["stats"]["spp"] == 8
    assert scene["spec"]["spectral"]["mode"] == "lw" and scene["renders"] == [r["render_id"]]
    assert [x["render_id"] for x in listing] == [r["render_id"]]
    assert Path(r["output"]).is_file()


def test_errors_are_tool_errors(gas: Path, tmp_path: Path):
    async def scenario():
        async with Client(create_server(config(tmp_path))) as c:
            sid = (await call(c, "build_scene", spec=ir_spec(gas)))["scene_id"]
            rid = (await call(c, "render", scene_id=sid, spp=2))["render_id"]
            cases = [
                ("render", {"scene_id": "s000000000000", "spp": 4}, "unknown scene"),
                ("render", {"scene_id": "../../etc", "spp": 4}, "unknown scene"),
                ("render", {"scene_id": sid, "spp": 0}, "spp must be between"),
                ("read_stats", {"render_id": "nope"}, "unknown render"),
                ("read_stats", {"render_id": rid, "percentile": 42}, "percentile must be one of"),
                ("read_stats", {"render_id": rid, "target_rel_err": 5}, "is a fraction"),
            ]
            out = []
            for tool, args, match in cases:
                with pytest.raises(ToolError, match=match):
                    await call(c, tool, **args)
                out.append(tool)
            return out

    assert len(asyncio.run(scenario())) == 6


def test_failed_and_timed_out_renders_return_their_status(gas: Path, tmp_path: Path):
    async def scenario(timeout_s: float, **env):
        async with Client(create_server(config(tmp_path, **env))) as c:
            sid = (await call(c, "build_scene", spec=ir_spec(gas)))["scene_id"]
            r = await call(c, "render", scene_id=sid, spp=4, timeout_s=timeout_s)
            rec = await read(c, f"htrdr://renders/{r['render_id']}")
            with pytest.raises(ToolError, match="only finished renders"):
                await call(c, "read_stats", render_id=r["render_id"])
            return r, rec

    r, rec = asyncio.run(scenario(1.5, FAKE_HTRDR_SLEEP="30"))
    assert r["status"] == "timeout" and rec["status"] == "timeout" and r["wall_s"] < 10
    r, rec = asyncio.run(scenario(60, FAKE_HTRDR_FAIL="1"))
    assert r["status"] == "failed" and "simulated failure" in r["stderr_tail"][-1]


def test_state_survives_a_server_restart(gas: Path, tmp_path: Path):
    async def first():
        async with Client(create_server(config(tmp_path))) as c:
            sid = (await call(c, "build_scene", spec=ir_spec(gas)))["scene_id"]
            return (await call(c, "render", scene_id=sid, spp=8))["render_id"]

    async def second(rid: str):
        async with Client(create_server(config(tmp_path))) as c:
            return await call(c, "read_stats", render_id=rid, target_rel_err=0.01)

    s = asyncio.run(second(asyncio.run(first())))
    assert s["spp"] == 8 and s["proposal"]["spp"] > 8


@pytest.mark.skipif(not HTPP.htpp.is_file(), reason="htpp isn't installed")
def test_postprocess_writes_pngs_with_htpp(gas: Path, tmp_path: Path):
    async def scenario():
        async with Client(create_server(config(tmp_path))) as c:
            lw = (await call(c, "build_scene", spec=ir_spec(gas)))["scene_id"]
            xyz = (await call(c, "build_scene", spec=xyz_spec(gas)))["scene_id"]
            lw_r = (await call(c, "render", scene_id=lw, spp=8))["render_id"]
            xyz_r = (await call(c, "render", scene_id=xyz, spp=8))["render_id"]
            a = await call(c, "postprocess", render_id=lw_r)
            b = await call(c, "postprocess", render_id=xyz_r, exposure=2)
            tb = await call(c, "postprocess", render_id=lw_r, mode="map", component=0, palette="viridis")
            with pytest.raises(ToolError, match="needs a CIE XYZ render"):
                await call(c, "postprocess", render_id=lw_r, mode="image")
            with pytest.raises(ToolError, match="both range_min and range_max"):
                await call(c, "postprocess", render_id=lw_r, range_min=0)
            return a, b, tb

    a, b, tb = asyncio.run(scenario())
    for out in (a, b, tb):
        png = Path(out["png"]).read_bytes()
        assert png[:8] == b"\x89PNG\r\n\x1a\n"
        assert (int.from_bytes(png[16:20], "big"), int.from_bytes(png[20:24], "big")) == (64, 48)
        assert (out["width"], out["height"]) == (64, 48)
    assert "-m pixcpnt=2:palette=inferno" in a["command_line"]
    assert "-i exposure=2" in b["command_line"]
    assert any("prefer the radiance" in n for n in tb["notes"])
