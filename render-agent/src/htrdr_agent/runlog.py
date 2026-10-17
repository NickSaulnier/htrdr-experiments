"""The agent's run log: runs/agent/<run_id>/run.json (everything) and run.md (for people).

The Markdown log answers:
- what was asked, and how it was read (every assumption);
- what htrdr ran: the exact command lines, the input files' SHA-256, and
  what built htrdr (it has no version option);
- why each next step was taken and by whom (rule, LLM or person), with the
  measured numbers written by code;
- what came out, how to reproduce it, and links that open it in the
  browser viewer (served by its dev server from the repo root).
"""

from __future__ import annotations

import json
import re
import subprocess
import time
from pathlib import Path
from typing import Any
from urllib.parse import quote

PACKAGE_DIR = Path(__file__).resolve().parent


def _pct(x: float | None) -> str:
    return "—" if x is None else f"{100 * x:.3g} %"


def _secs(x: float | None) -> str:
    return "—" if x is None else f"{x:.2f} s"


def agent_version() -> dict[str, Any]:
    """The agent's own code version: its git commit, and whether the tree had local changes."""
    def git(*args: str) -> str | None:
        try:
            r = subprocess.run(["git", "-C", str(PACKAGE_DIR), *args], capture_output=True, text=True, timeout=10)
        except (OSError, subprocess.TimeoutExpired):
            return None
        return r.stdout.strip() if r.returncode == 0 else None

    commit = git("rev-parse", "--short", "HEAD")
    if commit is None:
        return {"commit": None}
    return {"commit": commit, "local_changes": bool(git("status", "--porcelain", "--", "."))}


def viewer_settings(spec: dict[str, Any]) -> str:
    """Hash settings that show a render sensibly in the viewer."""
    spectral = spec["spectral"]
    if spectral["mode"] == "cie_xyz":
        return "m=image"
    band = spectral.get("range_nm") or [0, 0]
    # Monochromatic lw: htrdr 0.12's brightness temperature is wrong, so map the radiance.
    pair = 0 if spectral["mode"] == "lw" and band[0] != band[1] else 1
    return f"m=map&q={pair}&s=value"


def viewer_link(path: str | None, viewer: tuple[str, Path] | None, settings: str = "", b: str | None = None) -> str | None:
    """A link that opens `path` (and `b`) in the viewer, if both lie under the directory it serves."""
    if not path or viewer is None:
        return None
    url, root = viewer

    def served(p: str) -> str | None:
        try:
            rel = Path(p).resolve().relative_to(Path(root).resolve())
        except ValueError:
            return None
        return "/" + quote(rel.as_posix())

    a_path = served(path)
    b_path = served(b) if b else None
    if a_path is None or (b and b_path is None):
        return None
    parts = [f"a={a_path}"] + ([f"b={b_path}"] if b_path else []) + ([settings] if settings else [])
    return f"{url.rstrip('/')}/#{'&'.join(parts)}"


def render_markdown(state: dict[str, Any], viewer: tuple[str, Path] | None = None) -> str:
    lines = [f"# Render run `{state['run_id']}`", ""]
    lines += [f"**Request:** {state['text']}", ""]
    lines += [f"**Outcome:** {state.get('status')}: {state.get('message') or ''}".rstrip(), ""]
    final = state.get("final") or {}
    renders = state.get("renders", [])
    ok = [r for r in renders if r.get("status") == "ok"]
    spec = state.get("spec")

    if final.get("image"):
        lines += [f"- **Image data:** `{final['image']}`"]
        if final.get("png"):
            lines += [f"- **PNG:** `{final['png']}`"]
        if spec and (link := viewer_link(final["image"], viewer, viewer_settings(spec))):
            lines += [f"- **Open in the viewer:** <{link}>"]
        if spec and len(ok) >= 2:
            pair = 1
            compare = viewer_link(ok[-1]["output"], viewer, f"cmp=diff&dv=z&k=2&q={pair}", b=ok[-2]["output"])
            if compare:
                lines += [f"- **Final vs previous render** (z = Δ/σΔ; about 5 % of pixels beyond 2σ is just noise): <{compare}>"]
        if final.get("elapsed_s") is not None:
            lines += [f"- **Elapsed:** {_secs(final['elapsed_s'])} (renders {_secs(state.get('spent_s'))})"]
        lines.append("")

    if state.get("assumptions"):
        lines += ["## How the request was read", ""]
        lines += [f"- {a}" for a in state["assumptions"]] + [""]
    if state.get("warnings"):
        lines += ["## Scene warnings", ""]
        lines += [f"- {w}" for w in state["warnings"]] + [""]

    target = state.get("target")
    if renders:
        lines += ["## Renders", "", f"Target: p95 relative error ≤ {_pct(target)}; budget {_secs(state.get('budget_s'))}, "
                  f"spent {_secs(state.get('spent_s'))}.", ""]
        lines += ["| # | spp | status | wall | predicted | p50 | p95 |", "|---|---|---|---|---|---|---|"]
        for i, r in enumerate(renders, 1):
            status = r["status"] + (f" (after {r['retries']} retry)" if r.get("retries") else "")
            lines.append(f"| {i} | {r['spp']} | {status} | {_secs(r.get('wall_s'))} | {_secs(r.get('predicted_wall_s'))} "
                         f"| {_pct(r.get('p50'))} | {_pct(r.get('p95'))} |")
        retried = [r for r in renders if r.get("retries")]
        if retried:
            def threads(r: dict) -> str:
                m = re.search(r"(?:^| )-t (\d+)(?: |$)", r.get("command_line", ""))
                return f" on {m.group(1)} threads" if m else ""

            lines += ["", "Retried: " + "; ".join(f"`{r['render_id']}` died of SIGFPE in htrdr and was rerun{threads(r)}" for r in retried)
                      + ". Each render's record (`htrdr://renders/{id}`) keeps the failed attempt's command line."]
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

    if ok:
        lines += ["## Reproduce", "", "The final render, exactly as run (htrdr's libraries are found through `LD_LIBRARY_PATH=<prefix>/lib`):", "",
                  "```sh", ok[-1]["command_line"], "```", ""]
        if final.get("png_command"):
            lines += ["And its PNG:", "", "```sh", final["png_command"], "```", ""]

    env = final.get("environment") or {}
    inputs = state.get("input_sha256") or {}
    if env or inputs:
        lines += ["## Environment", ""]
        if env.get("htrdr_version"):
            built = f", built {env['built_at']}" if env.get("built_at") else ""
            lines.append(f"- htrdr {env['htrdr_version']} (`{env.get('htrdr')}`, SHA-256 `{env.get('htrdr_sha256', '?')[:16]}…`{built})")
        elif env.get("htrdr"):
            lines.append(f"- htrdr: `{env['htrdr']}`{' — ' + env['note'] if env.get('note') else ''}")
        if env.get("htpp_version"):
            lines.append(f"- {env['htpp_version']}")
        components = env.get("components") or {}
        if components:
            lines.append("- Built from: " + ", ".join(f"{name} {c['tag']} ({c['commit']})" for name, c in components.items()))
        agent = env.get("agent") or {}
        if agent.get("commit"):
            lines.append(f"- render agent at commit `{agent['commit']}`{' with local changes' if agent.get('local_changes') else ''}")
        llm = env.get("llm") or {}
        if llm:
            lines.append(f"- LLM: `{llm.get('model')}` at {llm.get('url')}; decisions by {llm.get('decisions', 'llm')}")
        for path, digest in inputs.items():
            lines.append(f"- input `{path}`: SHA-256 `{digest}`")
        lines.append("")
    return "\n".join(lines)


def write_run_log(state: dict[str, Any], runs_dir: Path | str, viewer: tuple[str, Path] | None = None) -> Path:
    """Write run.json and run.md; returns the Markdown path.

    `viewer` is the viewer's URL and the directory it serves, for links.
    """
    out = Path(runs_dir) / "agent" / state["run_id"]
    out.mkdir(parents=True, exist_ok=True)
    data = {**state, "written_at": time.strftime("%Y-%m-%dT%H:%M:%S%z")}
    (out / "run.json").write_text(json.dumps(data, indent=2, default=str))
    md = out / "run.md"
    md.write_text(render_markdown(data, viewer))
    return md
