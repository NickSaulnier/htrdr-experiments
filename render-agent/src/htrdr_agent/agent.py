"""The render agent: a LangGraph loop over the MCP tools.

    parse ─► build ─► render ─► stats ─► decide ─┬─► approve ─► render   (next render)
      │                  ▲                       ├──────────────► render (no approval needed)
      └─► finish         └───────────────────────┴──────────────► finish (target met, budget, stop)

- parse:   the LLM reads the request into a RenderRequest (llm.py); code
           turns it into a SceneSpec, a target and a budget (request.py).
- render:  one MCP `render` call; the first is a cheap preview.
- stats:   MCP `read_stats`, with the target and the remaining budget, which
           returns the proposal for the next render.
- decide:  code lists the options it allows (the proposal, the largest render
           the budget still affords, stop) with their predicted cost; the LLM
           picks one and says why. Invalid or failed answers fall back to the
           rule's choice. Reaching the target ends the run without asking.
- approve: renders predicted to take longer than a threshold wait for a
           person (LangGraph interrupt). This node does nothing else, because
           a resumed node reruns from its start.
- finish:  a PNG of the last render (best effort) and the run log.

State is plain JSON and checkpointed, so an interrupted run resumes in a
new process (`htrdr-agent --resume RUN_ID`). Tools, parser and decider are
injected, not stored in state.
"""

from __future__ import annotations

import json
import math
import os
import time
import uuid
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal, Protocol, TypedDict

from pydantic import BaseModel, Field

from .llm import LLMConfig, parse_request
from .request import RenderRequest, RequestError, resolve_spectral, target_percent, target_rel_err, to_scene_spec
from .runlog import write_run_log
from .scene import Inputs

Status = Literal["running", "done", "stopped", "needs_input", "failed"]
Action = Literal["render", "render_affordable", "stop"]


class AgentState(TypedDict, total=False):
    run_id: str
    text: str
    status: Status
    message: str | None
    request: dict[str, Any] | None
    assumptions: list[str]
    spec: dict[str, Any] | None
    scene_id: str | None
    warnings: list[str]
    target: float
    budget_s: float
    spent_s: float
    next_spp: int | None
    pending: dict[str, Any] | None
    """The render waiting for approval: spp, predicted wall time, option."""
    renders: list[dict[str, Any]]
    decisions: list[dict[str, Any]]
    final: dict[str, Any] | None
    started_at: float


class Tools(Protocol):
    async def build_scene(self, spec: dict) -> dict: ...
    async def render(self, scene_id: str, spp: int, timeout_s: float | None) -> dict: ...
    async def read_stats(self, render_id: str, target_rel_err: float, max_spp: int, budget_s: float) -> dict: ...
    async def postprocess(self, render_id: str) -> dict: ...


Parser = Callable[[str], Awaitable[RenderRequest]]


@dataclass(frozen=True)
class Option:
    id: Action
    spp: int | None
    predicted_wall_s: float | None
    note: str


class Choice(BaseModel):
    """The LLM's pick among the options code allows."""

    option: str = Field(description="The id of the chosen option.")
    reason: str = Field(description="One or two sentences on why, without numbers, for the run log.")


def facts(state: AgentState, last: dict, option: Option | None = None) -> str:
    """The measured situation behind a decision, written by code so the log's numbers are exact."""
    remaining = state["budget_s"] - state.get("spent_s", 0.0)
    text = f"p95 {100 * last['p95']:.3g} % vs target {100 * state['target']:.3g} % after {last['spp']} spp; {remaining:.0f} s of budget left"
    if option is not None and option.spp is not None:
        text += f"; next: {option.spp} spp, predicted {option.predicted_wall_s:.1f} s"
    return text + "."


Decider = Callable[[dict, list[Option]], Awaitable[Choice]]


def _default_inputs() -> Inputs:
    data = Path(os.environ.get("HTRDR_DATA", Path.home() / "htrdr-data"))
    return Inputs(atmosphere=data / "etc" / "ecrad_opt_prop.txt")


@dataclass
class AgentConfig:
    inputs: Inputs = field(default_factory=_default_inputs)
    preview_spp: int = 8
    max_iterations: int = 8
    """Renders per run, preview included."""
    max_spp: int = 1_000_000
    default_budget_s: float = 1800.0
    """Wall-time budget for a run's renders, when the request doesn't state one."""
    approve_over_s: float | None = 120.0
    """Renders predicted to take longer wait for approval; None never asks."""
    runs_dir: Path = Path("runs")


# --- decisions ------------------------------------------------------------


def options_for(state: AgentState, stats: dict, config: AgentConfig) -> list[Option]:
    """The next steps code allows, best first; stop is always last and always allowed."""
    remaining = state["budget_s"] - state.get("spent_s", 0.0)
    last_spp = state["renders"][-1]["spp"]
    proposal = stats.get("proposal") or {}
    model = stats.get("cost_model")
    out: list[Option] = []
    if proposal and not proposal["capped"] and proposal["predicted_wall_s"] <= remaining and proposal["spp"] > last_spp:
        kind = "an intermediate step (spp grows at most 16x)" if proposal["stepped"] else "should reach the target"
        out.append(Option("render", proposal["spp"], proposal["predicted_wall_s"], f"the proposal; {kind}"))
    elif model and model["per_spp_s"] > 0:
        affordable = math.floor((remaining * 0.9 - model["fixed_s"]) / model["per_spp_s"])
        affordable = min(affordable, config.max_spp, last_spp * 16)
        if affordable > last_spp * 1.25:
            predicted = model["fixed_s"] + model["per_spp_s"] * affordable
            out.append(Option("render_affordable", affordable, predicted,
                              f"the largest render the remaining {remaining:.0f} s affords; the target will likely be missed"))
    out.append(Option("stop", None, None, "accept the current render and finish"))
    return out


def rule_choice(options: list[Option]) -> Choice:
    best = options[0]
    reasons = {"render": "The proposal fits the budget.", "render_affordable": "The proposal doesn't fit the budget; render what it affords.",
               "stop": "No further render fits the budget or the limits."}
    return Choice(option=best.id, reason=reasons[best.id])


DECIDE_PROMPT = """\
You supervise a Monte Carlo radiative transfer render. The last render didn't reach the target
uncertainty yet. Choose the next step among the options given, which already respect the time budget.
Prefer rendering on while an option exists, unless the statistics or notes show the render is wrong
(e.g. no usable pixels, or the view can't show what was asked); then stop and say why.
Reply with the option id and a short reason. Don't quote numbers in the reason: the measured errors,
predictions and budget are recorded separately, exactly. Say only what you judged and why."""


def llm_decider(config: LLMConfig) -> Decider:
    async def decide(context: dict, options: list[Option]) -> Choice:
        schema = Choice.model_json_schema()
        schema["properties"]["option"]["enum"] = [o.id for o in options]  # only allowed options can be chosen
        client = config.client()
        completion = await client.chat.completions.create(
            model=config.model,
            messages=[
                {"role": "system", "content": DECIDE_PROMPT},
                {"role": "user", "content": json.dumps({"situation": context, "options": [o.__dict__ for o in options]}, default=str)},
            ],
            temperature=0,
            max_tokens=512,
            response_format={"type": "json_schema", "json_schema": {"name": "Choice", "schema": schema, "strict": True}},  # type: ignore[arg-type]
            extra_body={"chat_template_kwargs": {"enable_thinking": False}},
        )
        return Choice.model_validate_json(completion.choices[0].message.content or "")

    return decide


def llm_parser(config: LLMConfig) -> Parser:
    async def parse(text: str) -> RenderRequest:
        return (await parse_request(text, config)).request

    return parse


# --- the graph ------------------------------------------------------------


def build_graph(tools: Tools, parser: Parser, decider: Decider | None, config: AgentConfig, checkpointer=None):
    from langgraph.graph import END, START, StateGraph
    from langgraph.types import interrupt

    def finish_with(status: Status, message: str) -> AgentState:
        return {"status": status, "message": message}

    async def parse(state: AgentState) -> AgentState:
        try:
            req = await parser(state["text"])
        except Exception as e:  # noqa: BLE001 - an unreadable reply ends the run, it doesn't crash it
            return finish_with("failed", f"couldn't read the request: {type(e).__name__}: {e}")
        if not req.is_render_request:
            return {"request": req.model_dump(), **finish_with("needs_input", req.clarification or "That isn't a render request.")}
        try:
            spec = to_scene_spec(req, config.inputs)
            notes = resolve_spectral(req).notes
            target = target_rel_err(req)
        except RequestError as e:
            return {"request": req.model_dump(), **finish_with("needs_input", str(e))}
        assumptions = [*req.assumptions, *notes]
        if req.target_rel_err_percent is None:
            assumptions.append(f"target uncertainty {target_percent(req):g} % (p95 of the per-pixel relative error)")
        budget = req.time_budget_s or config.default_budget_s
        if req.time_budget_s is None:
            assumptions.append(f"time budget {budget:g} s for the renders")
        return {
            "request": req.model_dump(), "spec": json.loads(spec.model_dump_json()), "assumptions": assumptions,
            "target": target, "budget_s": float(budget), "next_spp": config.preview_spp,
        }

    async def build(state: AgentState) -> AgentState:
        try:
            scene = await tools.build_scene(state["spec"])  # type: ignore[arg-type]
        except Exception as e:  # noqa: BLE001
            return finish_with("failed", f"build_scene failed: {e}")
        return {"scene_id": scene["scene_id"], "warnings": scene["warnings"]}

    async def render(state: AgentState) -> AgentState:
        remaining = state["budget_s"] - state.get("spent_s", 0.0)
        spp = state["next_spp"]
        assert spp is not None
        try:
            r = await tools.render(state["scene_id"], spp, timeout_s=max(remaining, 30.0))  # type: ignore[arg-type]
        except Exception as e:  # noqa: BLE001
            return finish_with("failed", f"render failed: {e}")
        record = {k: r.get(k) for k in ("render_id", "spp", "status", "wall_s", "predicted_wall_s", "command_line", "output", "stderr_tail")}
        out: AgentState = {"renders": [*state.get("renders", []), record], "spent_s": state.get("spent_s", 0.0) + r["wall_s"], "pending": None}
        if r["status"] != "ok":
            out |= finish_with("failed", f"render {r['render_id']} {r['status']}: {r['message']}")
        return out

    async def stats(state: AgentState) -> AgentState:
        last = state["renders"][-1]
        remaining = state["budget_s"] - state.get("spent_s", 0.0)
        try:
            s = await tools.read_stats(last["render_id"], state["target"], config.max_spp, max(remaining, 0.0))
        except Exception as e:  # noqa: BLE001
            return finish_with("failed", f"read_stats failed: {e}")
        last = {**last, "p95": s["error"]["percentiles"]["p95"], "p50": s["error"]["percentiles"]["p50"],
                "proposal": s.get("proposal"), "notes": s.get("notes", []), "quantities": s.get("quantities"),
                "cost_model": s.get("cost_model")}
        return {"renders": [*state["renders"][:-1], last]}

    async def decide(state: AgentState) -> AgentState:
        last = state["renders"][-1]
        proposal = last.get("proposal") or {}
        n = len(state["renders"])
        record = {"after": last["render_id"], "p95": last["p95"], "target": state["target"]}
        if proposal.get("target_met"):
            choice, by = Choice(option="stop", reason=f"Target met: p95 {last['p95']:.4g} ≤ {state['target']:.4g}."), "rule"
            done: Status = "done"
            return {"decisions": [*state.get("decisions", []), {**record, "action": "stop", "reason": choice.reason, "by": by}],
                    "next_spp": None, **finish_with(done, choice.reason)}
        if n >= config.max_iterations:
            reason = f"Stopped after {n} renders (max_iterations), with p95 {last['p95']:.4g} above the target {state['target']:.4g}."
            return {"decisions": [*state.get("decisions", []), {**record, "action": "stop", "reason": reason, "by": "rule"}],
                    **finish_with("stopped", reason)}
        opts = options_for(state, {"proposal": proposal, "cost_model": last.get("cost_model")}, config)
        choice, by = rule_choice(opts), "rule"
        if decider is not None and len(opts) > 1:
            # Percentages with explicit names: bare fractions next to other numbers got misread.
            context = {
                "request": state["text"],
                "target_p95_rel_err_percent": round(100 * state["target"], 4),
                "renders_so_far": len(state["renders"]),
                "budget_remaining_s": round(state["budget_s"] - state.get("spent_s", 0.0), 1),
                "last_render": {
                    "spp": last["spp"], "wall_s": last["wall_s"],
                    "p50_rel_err_percent": round(100 * last["p50"], 4),
                    "p95_rel_err_percent": round(100 * last["p95"], 4),
                    "notes": last.get("notes", []),
                },
                "scene_warnings": state.get("warnings", []),
            }
            try:
                picked = await decider(context, opts)
                if picked.option in {o.id for o in opts}:
                    choice, by = picked, "llm"
                else:
                    by = f"rule (the LLM chose {picked.option!r}, which isn't allowed)"
            except Exception as e:  # noqa: BLE001
                by = f"rule (the LLM failed: {type(e).__name__})"
        option = next(o for o in opts if o.id == choice.option)
        entry = {**record, "action": option.id, "spp": option.spp, "predicted_wall_s": option.predicted_wall_s,
                 "facts": facts(state, last, option), "reason": choice.reason, "by": by, "options": [o.__dict__ for o in opts]}
        decisions = [*state.get("decisions", []), entry]
        if option.id == "stop":
            status: Status = "stopped"
            return {"decisions": decisions, **finish_with(status, f"Stopped: {facts(state, last)} {choice.reason}")}
        return {"decisions": decisions, "next_spp": option.spp,
                "pending": {"spp": option.spp, "predicted_wall_s": option.predicted_wall_s, "option": option.id}}

    async def approve(state: AgentState) -> AgentState:
        # Nothing but the interrupt: on resume this node reruns from the top.
        p = state["pending"]
        answer = interrupt({
            "question": f"Render at {p['spp']} spp? Predicted {p['predicted_wall_s']:.0f} s.",
            "spp": p["spp"], "predicted_wall_s": p["predicted_wall_s"], "run_id": state["run_id"],
        })
        approved = answer is True or (isinstance(answer, dict) and answer.get("approve") is True)
        decision = {"after": state["renders"][-1]["render_id"], "action": "approval", "approved": approved,
                    "spp": p["spp"], "by": "user", "reason": "approved" if approved else "declined"}
        out: AgentState = {"decisions": [*state["decisions"], decision]}
        if not approved:
            out |= finish_with("stopped", f"The render at {p['spp']} spp (predicted {p['predicted_wall_s']:.0f} s) was declined.")
        return out

    async def finish(state: AgentState) -> AgentState:
        final: dict[str, Any] = {}
        ok = [r for r in state.get("renders", []) if r.get("status") == "ok"]
        if ok:
            final["render_id"] = ok[-1]["render_id"]
            final["image"] = ok[-1]["output"]
            try:
                final["png"] = (await tools.postprocess(ok[-1]["render_id"]))["png"]
            except Exception as e:  # noqa: BLE001 - a missing PNG doesn't fail the run
                final["png_error"] = str(e)
        status = state.get("status", "running")
        if status == "running":
            status = "done"
        out: AgentState = {"final": final, "status": status}
        log = write_run_log({**state, **out}, config.runs_dir)  # type: ignore[arg-type]
        out["final"] = {**final, "run_log": str(log)}
        return out

    def after(node_ok: str) -> Callable[[AgentState], str]:
        def route(state: AgentState) -> str:
            return "finish" if state.get("status", "running") != "running" else node_ok
        return route

    def after_decide(state: AgentState) -> str:
        if state.get("status", "running") != "running":
            return "finish"
        p = state["pending"]
        needs_approval = config.approve_over_s is not None and (p["predicted_wall_s"] or 0) > config.approve_over_s
        return "approve" if needs_approval else "render"

    g = StateGraph(AgentState)
    for name, fn in (("parse", parse), ("build", build), ("render", render), ("stats", stats),
                     ("decide", decide), ("approve", approve), ("finish", finish)):
        g.add_node(name, fn)
    g.add_edge(START, "parse")
    g.add_conditional_edges("parse", after("build"), ["build", "finish"])
    g.add_conditional_edges("build", after("render"), ["render", "finish"])
    g.add_conditional_edges("render", after("stats"), ["stats", "finish"])
    g.add_conditional_edges("stats", after("decide"), ["decide", "finish"])
    g.add_conditional_edges("decide", after_decide, ["approve", "render", "finish"])
    g.add_conditional_edges("approve", after("render"), ["render", "finish"])
    g.add_edge("finish", END)
    return g.compile(checkpointer=checkpointer)


def new_run(text: str) -> AgentState:
    run_id = time.strftime("a%Y%m%d-%H%M%S-") + uuid.uuid4().hex[:6]
    return {"run_id": run_id, "text": text, "status": "running", "renders": [], "decisions": [],
            "spent_s": 0.0, "warnings": [], "assumptions": [], "started_at": time.time()}


# --- tools over MCP -------------------------------------------------------


class McpTools:
    """The agent's Tools, as calls to the htrdr MCP server through a FastMCP client."""

    def __init__(self, client):
        self.client = client

    async def _call(self, name: str, **args) -> dict:
        return (await self.client.call_tool(name, args)).structured_content

    async def build_scene(self, spec: dict) -> dict:
        return await self._call("build_scene", spec=spec)

    async def render(self, scene_id: str, spp: int, timeout_s: float | None) -> dict:
        return await self._call("render", scene_id=scene_id, spp=spp, timeout_s=timeout_s)

    async def read_stats(self, render_id: str, target_rel_err: float, max_spp: int, budget_s: float) -> dict:
        return await self._call("read_stats", render_id=render_id, target_rel_err=target_rel_err, max_spp=max_spp, budget_s=budget_s)

    async def postprocess(self, render_id: str) -> dict:
        return await self._call("postprocess", render_id=render_id)
