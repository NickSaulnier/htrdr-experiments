"""The agent's run log: runs/agent/<run_id>/run.json (everything) and run.md (for people).

The Markdown log answers: what was asked, how it was read (with every
assumption), what htrdr ran (exact command lines), why each next step was
taken and by whom (rule, LLM or person), and what came out.
"""

from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any


def _pct(x: float | None) -> str:
    return "—" if x is None else f"{100 * x:.3g} %"


def _secs(x: float | None) -> str:
    return "—" if x is None else f"{x:.2f} s"


def render_markdown(state: dict[str, Any]) -> str:
    lines = [f"# Render run `{state['run_id']}`", ""]
    lines += [f"**Request:** {state['text']}", ""]
    lines += [f"**Outcome:** {state.get('status')}: {state.get('message') or ''}".rstrip(), ""]
    final = state.get("final") or {}
    if final.get("png"):
        lines += [f"**Image:** `{final['png']}` (data: `{final.get('image')}`)", ""]
    elif final.get("image"):
        lines += [f"**Image data:** `{final['image']}`", ""]

    if state.get("assumptions"):
        lines += ["## How the request was read", ""]
        lines += [f"- {a}" for a in state["assumptions"]] + [""]
    if state.get("warnings"):
        lines += ["## Scene warnings", ""]
        lines += [f"- {w}" for w in state["warnings"]] + [""]

    target = state.get("target")
    renders = state.get("renders", [])
    if renders:
        lines += ["## Renders", "", f"Target: p95 relative error ≤ {_pct(target)}; budget {_secs(state.get('budget_s'))}, "
                  f"spent {_secs(state.get('spent_s'))}.", ""]
        lines += ["| # | spp | status | wall | predicted | p50 | p95 |", "|---|---|---|---|---|---|---|"]
        for i, r in enumerate(renders, 1):
            lines.append(f"| {i} | {r['spp']} | {r['status']} | {_secs(r.get('wall_s'))} | {_secs(r.get('predicted_wall_s'))} "
                         f"| {_pct(r.get('p50'))} | {_pct(r.get('p95'))} |")
        lines += ["", "### Exact command lines", ""]
        for i, r in enumerate(renders, 1):
            lines += [f"{i}. `{r['render_id']}`", "", "   ```sh", f"   {r['command_line']}", "   ```", ""]
        # The final render's notes: earlier renders say the same about noisier data.
        notes = renders[-1].get("notes", [])
        if notes:
            lines += ["### Notes from the statistics (final render)", ""] + [f"- {n}" for n in notes] + [""]

    if state.get("decisions"):
        lines += ["## Decisions", ""]
        for d in state["decisions"]:
            what = d["action"] if d["action"] != "approval" else f"approval of {d.get('spp')} spp: {'yes' if d.get('approved') else 'no'}"
            spp = f" at {d['spp']} spp" if d.get("spp") and d["action"] != "approval" else ""
            facts = f" {d['facts']}" if d.get("facts") else ""
            lines.append(f"- after `{d['after']}`: **{what}**{spp}, by {d['by']}.{facts} {d.get('reason', '')}".rstrip())
        lines.append("")
    return "\n".join(lines)


def write_run_log(state: dict[str, Any], runs_dir: Path | str) -> Path:
    """Write run.json and run.md; returns the Markdown path."""
    out = Path(runs_dir) / "agent" / state["run_id"]
    out.mkdir(parents=True, exist_ok=True)
    data = {**state, "written_at": time.strftime("%Y-%m-%dT%H:%M:%S%z")}
    (out / "run.json").write_text(json.dumps(data, indent=2, default=str))
    md = out / "run.md"
    md.write_text(render_markdown(data))
    return md
