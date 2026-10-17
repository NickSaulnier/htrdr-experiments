"""The LangGraph agent end to end: fake htrdr behind the real MCP server, stub LLM."""

import asyncio
import json
import os
from dataclasses import replace
from pathlib import Path

import pytest
from fastmcp import Client
from langgraph.checkpoint.sqlite.aio import AsyncSqliteSaver
from langgraph.types import Command

from htrdr_agent.agent import AgentConfig, Choice, McpTools, build_graph, new_run
from htrdr_agent.request import RenderRequest
from htrdr_agent.runner import HtrdrInstall
from htrdr_agent.server import FAKE_HTRDR, ServerConfig, create_server

INSTALL = HtrdrInstall()


def ir_request(**over) -> RenderRequest:
    base = dict(
        spectral_words="infrared", wavelength_min_um=10.0, wavelength_max_um=10.0, sun_elevation_deg=40.0,
        sun_azimuth_deg=None, camera_altitude_m=2000.0, view_elevation_deg=0.0, view_azimuth_deg=0.0,
        fov_deg=60.0, image_width=64, image_height=48, target_rel_err_percent=1.0, quality_words="none",
        time_budget_s=None, assumptions=["view toward the horizon"], is_render_request=True, clarification=None,
    )
    return RenderRequest(**{**base, **over})


def stub_parser(req: RenderRequest):
    async def parse(text: str) -> RenderRequest:
        return req
    return parse


def server_config(runs: Path, speed: str = "0.02") -> ServerConfig:
    return ServerConfig(
        runs_dir=runs, htrdr=FAKE_HTRDR,
        htpp=[str(INSTALL.htpp)] if INSTALL.htpp.is_file() else None,
        env={**INSTALL.env(), "FAKE_HTRDR_SPEED": speed},
    )


def agent_config(gas: Path, runs: Path, **over) -> AgentConfig:
    from htrdr_agent.scene import Inputs
    return replace(AgentConfig(inputs=Inputs(atmosphere=gas), runs_dir=runs, approve_over_s=None), **over)


async def run(graph, payload, thread: str, answers: list | None = None) -> tuple[dict, list[dict]]:
    """Drive the graph to the end, answering interrupts from `answers`; returns the state and the questions."""
    config = {"configurable": {"thread_id": thread}, "recursion_limit": 200}
    questions = []
    answers = list(answers or [])
    while True:
        out = await graph.ainvoke(payload, config)
        if "__interrupt__" not in out:
            return out, questions
        questions.append(out["__interrupt__"][0].value)
        payload = Command(resume={"approve": answers.pop(0)})


async def scenario(tmp_path: Path, gas: Path, req: RenderRequest, *, decider=None, answers=None, speed="0.02", **cfg):
    runs = tmp_path / "runs"
    async with Client(create_server(server_config(runs, speed))) as client:
        graph = build_graph(McpTools(client), stub_parser(req), decider, agent_config(gas, runs, **cfg), checkpointer=None)
        if answers is not None:
            from langgraph.checkpoint.memory import InMemorySaver
            graph = build_graph(McpTools(client), stub_parser(req), decider, agent_config(gas, runs, **cfg), checkpointer=InMemorySaver())
        state = new_run("IR view at 10 µm, sun at 40° elevation, from 2 km altitude")
        return await run(graph, state, state["run_id"], answers)


def test_preview_then_refine_until_the_target_is_met(gas: Path, tmp_path: Path):
    state, _ = asyncio.run(scenario(tmp_path, gas, ir_request()))
    assert state["status"] == "done"
    renders = state["renders"]
    assert renders[0]["spp"] == 8  # the preview
    assert [r["spp"] for r in renders] == sorted(r["spp"] for r in renders) and len(renders) <= 4
    assert renders[-1]["p95"] <= 0.01
    assert state["decisions"][-1]["action"] == "stop" and "Target met" in state["decisions"][-1]["reason"]
    assert any("no effect in lw" in w for w in state["warnings"])
    assert any("1 %" not in a and "budget" in a for a in state["assumptions"])
    log = Path(state["final"]["run_log"]).read_text()
    for r in renders:
        assert r["command_line"] in log
    assert "Target met" in log and "## How the request was read" in log
    # Links that open the result in the viewer, which serves the runs directory's parent.
    final_id = renders[-1]["render_id"]
    assert f"#a=/runs/renders/{final_id}/image.txt&m=map&q=1&s=value" in log
    assert f"#a=/runs/renders/{final_id}/image.txt&b=/runs/renders/{renders[-2]['render_id']}/image.txt&cmp=diff" in log
    assert "## Reproduce" in log and renders[-1]["command_line"] in log.split("## Reproduce")[1]
    assert f"input `{gas}`: SHA-256 `" in log
    assert "not a real htrdr install" in log  # the fake
    data = json.loads((Path(state["final"]["run_log"]).parent / "run.json").read_text())
    assert data["status"] == "done" and len(data["renders"]) == len(renders)
    if INSTALL.htpp.is_file():
        assert Path(state["final"]["png"]).read_bytes()[:4] == b"\x89PNG"


def test_a_message_that_isnt_a_render_request_renders_nothing(gas: Path, tmp_path: Path):
    req = ir_request(is_render_request=False, clarification="Glad to help. What would you like rendered?")
    state, _ = asyncio.run(scenario(tmp_path, gas, req))
    assert state["status"] == "needs_input" and state["message"].startswith("Glad to help")
    assert state["renders"] == [] and "run_log" in state["final"]


def test_a_tight_budget_stops_short_of_the_target(gas: Path, tmp_path: Path):
    # At the fake's real-time speed (~0.03 s per spp here) the target needs ~45 s.
    state, _ = asyncio.run(scenario(tmp_path, gas, ir_request(time_budget_s=3.0), speed="1"))
    assert state["status"] == "stopped"
    assert state["renders"][-1]["p95"] > 0.01
    assert state["spent_s"] <= 3.0 * 1.5  # predictions can be off, but not by much
    assert state["decisions"][-1]["action"] == "stop"


def test_long_renders_wait_for_approval_and_can_be_declined(gas: Path, tmp_path: Path):
    declined, questions = asyncio.run(scenario(tmp_path / "no", gas, ir_request(), answers=[False], approve_over_s=0.0))
    assert declined["status"] == "stopped" and "declined" in declined["message"]
    assert len(declined["renders"]) == 1 and questions[0]["spp"] == declined["decisions"][0]["spp"]
    approved, questions = asyncio.run(scenario(tmp_path / "yes", gas, ir_request(), answers=[True] * 10, approve_over_s=0.0))
    assert approved["status"] == "done" and len(questions) == len(approved["renders"]) - 1
    assert [d["approved"] for d in approved["decisions"] if d["action"] == "approval"] == [True] * len(questions)


def test_a_run_resumes_from_its_checkpoint_in_a_new_process(gas: Path, tmp_path: Path):
    """Stop at the first approval, drop everything, then resume with fresh objects on the same files."""
    runs = tmp_path / "runs"
    db = str(tmp_path / "checkpoints.sqlite")
    cfg = agent_config(gas, runs, approve_over_s=0.0)

    async def first() -> str:
        async with Client(create_server(server_config(runs))) as client, AsyncSqliteSaver.from_conn_string(db) as saver:
            graph = build_graph(McpTools(client), stub_parser(ir_request()), None, cfg, checkpointer=saver)
            state = new_run("IR view at 10 µm")
            out = await graph.ainvoke(state, {"configurable": {"thread_id": state["run_id"]}})
            assert "__interrupt__" in out
            return state["run_id"]

    async def second(run_id: str) -> dict:
        async with Client(create_server(server_config(runs))) as client, AsyncSqliteSaver.from_conn_string(db) as saver:
            graph = build_graph(McpTools(client), stub_parser(ir_request()), None, cfg, checkpointer=saver)
            snapshot = await graph.aget_state({"configurable": {"thread_id": run_id}})
            assert snapshot.next == ("approve",) and len(snapshot.values["renders"]) == 1
            return (await run(graph, Command(resume={"approve": True}), run_id, answers=[True] * 10))[0]

    state = asyncio.run(second(asyncio.run(first())))
    assert state["status"] == "done" and state["renders"][0]["spp"] == 8 and len(state["renders"]) >= 2


def test_the_llm_may_only_pick_allowed_options(gas: Path, tmp_path: Path):
    calls: list[list[str]] = []

    async def rogue(context: dict, options) -> Choice:
        calls.append([o.id for o in options])
        return Choice(option="render_at_a_million_spp", reason="more is better")

    state, _ = asyncio.run(scenario(tmp_path, gas, ir_request(), decider=rogue))
    assert state["status"] == "done"
    llm_decisions = [d for d in state["decisions"] if "options" in d]
    assert llm_decisions and all("isn't allowed" in d["by"] for d in llm_decisions)
    assert all(ids[-1] == "stop" for ids in calls)


def test_a_failing_llm_falls_back_to_the_rules(gas: Path, tmp_path: Path):
    async def broken(context: dict, options) -> Choice:
        raise TimeoutError("vLLM is down")

    state, _ = asyncio.run(scenario(tmp_path, gas, ir_request(), decider=broken))
    assert state["status"] == "done"
    assert all(d["by"] == "rule (the LLM failed: TimeoutError)" for d in state["decisions"] if "options" in d)


def test_an_llm_that_stops_early_is_obeyed_and_logged(gas: Path, tmp_path: Path):
    async def cautious(context: dict, options) -> Choice:
        return Choice(option="stop", reason="The notes suggest the brightness temperature is unreliable.")

    state, _ = asyncio.run(scenario(tmp_path, gas, ir_request(), decider=cautious))
    assert state["status"] == "stopped" and len(state["renders"]) == 1
    assert state["decisions"][-1]["by"] == "llm" and "unreliable" in state["message"]


def test_the_iteration_limit_stops_the_loop(gas: Path, tmp_path: Path):
    state, _ = asyncio.run(scenario(tmp_path, gas, ir_request(target_rel_err_percent=0.01), max_iterations=2))
    assert state["status"] == "stopped" and len(state["renders"]) == 2
    assert "max_iterations" in state["message"]


LLM_URL = os.environ.get("HTRDR_LLM_URL", "http://127.0.0.1:8000/v1")


def _llm_up() -> bool:
    import urllib.request
    try:
        with urllib.request.urlopen(LLM_URL.rstrip("/") + "/models", timeout=2):
            return True
    except OSError:
        return False


@pytest.mark.llm
@pytest.mark.skipif(not _llm_up(), reason="no LLM endpoint")
def test_with_the_real_llm_on_the_fake_htrdr(gas: Path, tmp_path: Path):
    from htrdr_agent.agent import llm_decider, llm_parser
    from htrdr_agent.llm import LLMConfig

    async def go() -> dict:
        runs = tmp_path / "runs"
        llm = LLMConfig()
        async with Client(create_server(server_config(runs))) as client:
            graph = build_graph(McpTools(client), llm_parser(llm), llm_decider(llm), agent_config(gas, runs))
            state = new_run("IR view at 10 µm, sun at 40° elevation, from 2 km altitude")
            return (await run(graph, state, state["run_id"]))[0]

    state = asyncio.run(go())
    assert state["status"] == "done"
    assert state["spec"]["spectral"] == {"mode": "lw", "range_nm": [10000.0, 10000.0], "tref_k": None}
    assert state["spec"]["sensor"]["pos"] == [0.0, 0.0, 2000.0]
    assert any("no effect in lw" in w for w in state["warnings"])
    # Code writes the numbers behind every LLM decision.
    llm_made = [d for d in state["decisions"] if d["by"] == "llm"]
    assert llm_made and all(d["facts"].startswith("p95 ") for d in llm_made)
