"""htrdr-agent: render a natural-language request to a target uncertainty.

    htrdr-agent "IR view at 10 µm, sun at 40° elevation, from 2 km altitude"
    htrdr-agent --fake "..."            # the fake htrdr: no real renders
    htrdr-agent --resume RUN_ID         # continue a checkpointed run
    htrdr-agent --yes "..."             # approve long renders without asking

The MCP server runs in-process and the agent talks to it through a FastMCP
client, so every step goes through the same tools an external client sees.
Checkpoints: <runs>/agent/checkpoints.sqlite; run logs: <runs>/agent/<run_id>/.
"""

from __future__ import annotations

import argparse
import asyncio
import sys
from dataclasses import replace
from pathlib import Path

from .agent import AgentConfig, McpTools, build_graph, llm_decider, llm_parser, new_run
from .llm import LLMConfig
from .runner import HtrdrInstall
from .server import FAKE_HTRDR, ServerConfig, create_server


def _describe(node: str, update: dict) -> str | None:
    if not update:
        return None
    if node == "parse" and update.get("spec"):
        s = update["spec"]
        band = s["spectral"]["range_nm"]
        return f"parsed: {s['spectral']['mode']}{f' {band[0]:g}-{band[1]:g} nm' if band else ''}, camera at {s['sensor']['pos'][2]:g} m, " \
               f"target {100 * update['target']:g} %, budget {update['budget_s']:g} s"
    if node == "build":
        return f"scene {update.get('scene_id')}" + "".join(f"\n  warning: {w}" for w in update.get("warnings", []))
    if node == "render" and update.get("renders"):
        r = update["renders"][-1]
        return f"rendered {r['spp']} spp in {r['wall_s']:.2f} s ({r['status']})"
    if node == "stats" and update.get("renders"):
        r = update["renders"][-1]
        return f"  p95 {100 * r['p95']:.3g} %, p50 {100 * r['p50']:.3g} %"
    if node == "decide" and update.get("decisions"):
        d = update["decisions"][-1]
        spp = f" {d['spp']} spp (~{d['predicted_wall_s']:.1f} s)" if d.get("spp") else ""
        facts = f"\n  {d['facts']}" if d.get("facts") else ""
        return f"decide: {d['action']}{spp} [{d['by']}]{facts}\n  {d.get('reason', '')}"
    if node == "finish":
        f = update.get("final") or {}
        return f"finished: {update.get('status')}; log {f.get('run_log')}"
    return None


async def _drive(graph, payload, config: dict, auto_yes: bool) -> dict:
    from langgraph.types import Command

    while True:
        interrupted = None
        async for chunk in graph.astream(payload, config, stream_mode="updates"):
            for node, update in chunk.items():
                if node == "__interrupt__":
                    interrupted = update[0].value
                elif (line := _describe(node, update or {})) is not None:
                    print(line, flush=True)
        if interrupted is None:
            return (await graph.aget_state(config)).values
        question = interrupted["question"]
        if auto_yes:
            print(f"{question} yes (--yes)")
            approved = True
        elif sys.stdin.isatty():
            approved = input(f"{question} [y/N] ").strip().lower() in ("y", "yes")
        else:
            print(f"{question} no (not a terminal; pass --yes to approve)")
            approved = False
        payload = Command(resume={"approve": approved})


async def main_async(args: argparse.Namespace) -> int:
    from fastmcp import Client
    from langgraph.checkpoint.sqlite.aio import AsyncSqliteSaver

    runs = args.runs_dir.resolve()
    install = HtrdrInstall()
    server_config = ServerConfig.for_install(runs, install)
    if args.fake:
        server_config.htrdr = FAKE_HTRDR
    elif not install.available():
        print(f"htrdr-atmosphere not found at {install.binary}; build it or pass --fake", file=sys.stderr)
        return 2

    llm = LLMConfig()
    config = AgentConfig(runs_dir=runs, approve_over_s=None if args.approve_over is None or args.approve_over < 0 else args.approve_over)
    if args.max_iterations:
        config = replace(config, max_iterations=args.max_iterations)
    if args.fake:
        # The fake only checks that the gas file exists.
        gas = runs / "fake-gas.txt"
        gas.parent.mkdir(parents=True, exist_ok=True)
        gas.write_text("fake gas optical properties\n")
        config = replace(config, inputs=config.inputs.model_copy(update={"atmosphere": gas}))

    (runs / "agent").mkdir(parents=True, exist_ok=True)
    async with Client(create_server(server_config)) as client, \
            AsyncSqliteSaver.from_conn_string(str(runs / "agent" / "checkpoints.sqlite")) as saver:
        graph = build_graph(McpTools(client), llm_parser(llm), None if args.rules else llm_decider(llm), config, checkpointer=saver)
        if args.resume:
            run_id, payload = args.resume, None
            snapshot = await graph.aget_state({"configurable": {"thread_id": run_id}})
            if not snapshot.values:
                print(f"no run {run_id!r} in the checkpoints", file=sys.stderr)
                return 2
            if not snapshot.next:
                print(f"run {run_id} already finished: {snapshot.values.get('status')}")
                return 0
            if any(t.interrupts for t in snapshot.tasks):
                from langgraph.types import Command

                question = snapshot.tasks[0].interrupts[0].value["question"]
                approved = args.yes or (sys.stdin.isatty() and input(f"{question} [y/N] ").strip().lower() in ("y", "yes"))
                payload = Command(resume={"approve": approved})
        else:
            state = new_run(args.request)
            run_id, payload = state["run_id"], state
        print(f"run {run_id}")
        final = await _drive(graph, payload, {"configurable": {"thread_id": run_id}, "recursion_limit": 200}, args.yes)
    print(f"\n{final.get('status')}: {final.get('message')}")
    return 0 if final.get("status") in ("done", "stopped", "needs_input") else 1


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="htrdr-agent", description="Render a request with htrdr until a target uncertainty is met.")
    p.add_argument("request", nargs="?", help="what to render, in plain words")
    p.add_argument("--resume", metavar="RUN_ID", help="continue a checkpointed run")
    p.add_argument("--fake", action="store_true", help="use the fake htrdr")
    p.add_argument("--runs-dir", type=Path, default=Path("runs"))
    p.add_argument("--approve-over", type=float, default=120.0, metavar="S",
                   help="ask before renders predicted to take longer (default 120; negative never asks)")
    p.add_argument("--yes", action="store_true", help="approve long renders without asking")
    p.add_argument("--rules", action="store_true", help="decide with the rules only, without the LLM")
    p.add_argument("--max-iterations", type=int)
    args = p.parse_args(argv)
    if bool(args.request) == bool(args.resume):
        p.error("give a request or --resume RUN_ID, not both")
    return asyncio.run(main_async(args))


if __name__ == "__main__":
    raise SystemExit(main())
