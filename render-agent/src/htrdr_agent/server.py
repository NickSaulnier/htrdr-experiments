"""The MCP server: htrdr as four tools, and its run records as resources.

    build_scene   a SceneSpec in, a scene id out (idempotent), with warnings
    render        one htrdr run at a given spp, with live progress
    read_stats    error and cost summaries of a render, and the next spp to
                  reach a target uncertainty; never pixels
    postprocess   htpp's PNG of a render, for people and the viewer

    htrdr://renders              every render, oldest first
    htrdr://scenes/{scene_id}    a scene's spec, warnings and renders
    htrdr://renders/{render_id}  a render's record: the exact command line,
                                 status, timings, output tails, last stats

Run it over stdio with `htrdr-mcp` (`--fake` to use the fake htrdr).
"""

from __future__ import annotations

import argparse
import asyncio
import os
import signal
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal

from fastmcp import Context, FastMCP
from fastmcp.exceptions import ResourceError, ToolError
from pydantic import BaseModel

from .argv import build_argv, command_line
from .estimate import CostModel, propose
from .image import HtrdrImageError, load
from .png import ppm_to_png
from .runner import HtrdrInstall, file_sha256, progress_percent, run
from .scene import SceneSpec
from .stats import PERCENTILES, compute_stats
from .store import RenderRecord, SceneRecord, Store, dumps, now

PROGRESS_INTERVAL_S = 0.5
SIGFPE_RETRIES = 1
"""htrdr 0.12 sometimes dies of SIGFPE inside Star-SF's sampling when run on many threads
(seen on CIE XYZ renders of 470M paths at 30-32 threads; fine at 16 and 1). A render that
does is retried this many times with half the threads; both attempts are recorded."""
FAKE_HTRDR = [sys.executable, "-m", "htrdr_agent.fake"]


@dataclass
class ServerConfig:
    runs_dir: Path
    htrdr: list[str]
    """Command prefix for htrdr-atmosphere (the binary, or a wrapper such as the fake)."""
    htpp: list[str] | None = None
    env: dict[str, str] | None = None
    threads: int | None = None
    max_spp: int = 1_000_000
    default_timeout_s: float = 3600.0
    max_timeout_s: float = 24 * 3600.0
    check_inputs: bool = True
    """Refuse scenes whose input files don't exist where htrdr will look."""
    extra: dict[str, str] = field(default_factory=dict)
    install: HtrdrInstall | None = None
    """For htrdr://info; None when htrdr is a stand-in (the fake)."""

    @classmethod
    def for_install(cls, runs_dir: Path, install: HtrdrInstall | None = None, **kw) -> ServerConfig:
        install = install or HtrdrInstall()
        htpp = [str(install.htpp)] if install.htpp.is_file() else None
        return cls(runs_dir=runs_dir, htrdr=[str(install.binary)], htpp=htpp, env=install.env(), install=install, **kw)


class SceneResult(BaseModel):
    scene_id: str
    warnings: list[str]
    cache_key: str
    command_template: str
    """The command line every render of this scene runs, with <spp> and <output> to fill in."""
    input_sha256: dict[str, str]
    """SHA-256 of each input file, keyed by path."""


class RenderResult(BaseModel):
    render_id: str
    scene_id: str
    spp: int
    status: Literal["ok", "failed", "timeout", "cancelled"]
    wall_s: float
    predicted_wall_s: float | None
    command_line: str
    output: str
    message: str
    stderr_tail: list[str]
    """htrdr's last output lines; only filled in when the render didn't succeed."""
    retries: int = 0
    """Failed attempts before this result (htrdr's SIGFPE; see SIGFPE_RETRIES)."""


def create_server(config: ServerConfig) -> FastMCP:
    store = Store(config.runs_dir)
    render_lock = asyncio.Lock()  # one render at a time: htrdr already uses every core

    mcp = FastMCP(
        "htrdr",
        instructions=(
            "Render atmospheric radiative transfer images with htrdr-atmosphere. "
            "Call build_scene once with a full scene spec, then render it at a low spp as a preview, "
            "read_stats with a target relative error to get the spp the target needs and its predicted time, "
            "and render again. Monte Carlo error falls as 1/sqrt(spp). "
            "Every render's exact command line is kept in its htrdr://renders/{render_id} record."
        ),
    )

    def scene_or_error(scene_id: str) -> SceneRecord:
        rec = store.scene(scene_id)
        if rec is None:
            raise ToolError(f"unknown scene {scene_id!r}; call build_scene first")
        return rec

    def render_or_error(render_id: str) -> RenderRecord:
        rec = store.render(render_id)
        if rec is None:
            raise ToolError(f"unknown render {render_id!r}")
        return rec

    def cost_model(scene_id: str) -> CostModel | None:
        samples = [(r.spp, r.wall_s) for r in store.renders(scene_id) if r.status == "ok" and r.wall_s]
        return CostModel.fit(samples) if samples else None

    def argv_for(scene: SceneRecord, spp: int, output: Path | str, threads: int | None = None) -> list[str]:
        argv = build_argv(
            scene.spec, spp=spp, output=output, binary="htrdr-atmosphere",
            cache=store.cache_path(scene), threads=threads or config.threads, verbose=True,
        )
        return config.htrdr + argv[1:]

    @mcp.tool(annotations={"idempotentHint": True})
    def build_scene(spec: SceneSpec) -> SceneResult:
        """Register a scene to render: input files, spectral range, sun, sensor and image size.

        Returns a scene_id for render. The same spec always gives the same id.
        Read the warnings: they flag specs htrdr accepts that are probably not what was meant,
        e.g. a sun in longwave mode, where htrdr computes thermal emission only.
        """
        if config.check_inputs:
            i = spec.inputs
            missing = [str(p) for p in (i.atmosphere, i.clouds, i.mie, i.ground, i.materials) if p is not None and not Path(p).is_file()]
            if missing:
                raise ToolError(f"input files not found: {', '.join(missing)}")
        i = spec.inputs
        hashes = {str(p): file_sha256(p) for p in (i.atmosphere, i.clouds, i.mie, i.ground, i.materials) if p is not None and Path(p).is_file()}
        rec = store.put_scene(spec, hashes)
        template = command_line(argv_for(rec, 1, "<output>")).replace("spp=1", "spp=<spp>")
        return SceneResult(scene_id=rec.scene_id, warnings=rec.warnings, cache_key=rec.cache_key, command_template=template,
                           input_sha256=rec.input_sha256 or hashes)

    @mcp.tool
    async def render(scene_id: str, spp: int, ctx: Context, timeout_s: float | None = None) -> RenderResult:
        """Render a scene with htrdr at `spp` samples per pixel and wait for it.

        Reports progress while htrdr runs. Fails fast on a bad scene id or spp; a render that
        htrdr itself fails, or that times out, returns with that status and htrdr's last output.
        Renders run one at a time. Use read_stats on the returned render_id.
        """
        scene = scene_or_error(scene_id)
        if not 1 <= spp <= config.max_spp:
            raise ToolError(f"spp must be between 1 and {config.max_spp}")
        timeout = min(timeout_s or config.default_timeout_s, config.max_timeout_s)
        model = cost_model(scene_id)
        predicted = model.predict(spp) if model else None

        async with render_lock:
            render_id = store.new_render_id(scene_id, spp)
            output = store.render_dir(render_id) / "image.txt"
            argv = argv_for(scene, spp, output)
            rec = RenderRecord(
                render_id=render_id, scene_id=scene_id, spp=spp, status="running",
                command_line=command_line(argv), argv=argv, output=str(output), started_at=now(),
            )
            store.save_render(rec)

            latest: list[float] = []
            await ctx.report_progress(0, 100, f"{render_id}: starting")

            async def reporter() -> None:
                sent = -1.0
                while True:
                    await asyncio.sleep(PROGRESS_INTERVAL_S)
                    if latest and latest[-1] != sent:
                        sent = latest[-1]
                        await ctx.report_progress(sent, 100, f"{render_id}: rendering {sent:.0f}%")

            def on_line(line: str) -> None:
                if (pct := progress_percent(line)) is not None:
                    latest.append(pct)

            progress = asyncio.create_task(reporter())
            attempts: list[dict] = []
            threads = config.threads or os.cpu_count() or 2
            try:
                result = await run(argv, timeout_s=timeout, env=config.env, on_line=on_line)
                while result.returncode == -signal.SIGFPE and len(attempts) < SIGFPE_RETRIES and threads > 1:
                    attempts.append({
                        "command_line": result.command_line, "returncode": result.returncode,
                        "wall_s": round(result.wall_s, 3), "stderr_tail": result.stderr_tail[-10:],
                    })
                    threads = max(1, threads // 2)
                    argv = argv_for(scene, spp, output, threads=threads)
                    await ctx.report_progress(0, 100, f"{render_id}: htrdr died of SIGFPE; retrying on {threads} threads")
                    latest.clear()
                    result = await run(argv, timeout_s=timeout, env=config.env, on_line=on_line)
            except asyncio.CancelledError:
                store.save_render(rec.model_copy(update={"status": "cancelled", "finished_at": now(), "attempts": attempts}))
                raise
            finally:
                progress.cancel()

            # The record's command line is the attempt that produced the result.
            rec = rec.model_copy(update={
                "status": result.status, "returncode": result.returncode, "wall_s": round(result.wall_s, 3),
                "finished_at": now(), "stdout_tail": result.stdout_tail, "stderr_tail": result.stderr_tail,
                "argv": list(argv), "command_line": command_line(argv), "attempts": attempts,
            })
            store.save_render(rec)

        retried = f" after htrdr died of SIGFPE and was rerun on {threads} threads" if attempts else ""
        if result.ok:
            await ctx.report_progress(100, 100, f"{render_id}: done")
            message = f"Rendered in {result.wall_s:.2f} s{retried}. Call read_stats({render_id!r}) for its error and cost."
        elif result.status == "timeout":
            message = f"htrdr was stopped after the {timeout:g} s timeout."
        elif result.returncode == -signal.SIGFPE:
            message = f"htrdr died of SIGFPE (a floating-point fault){retried}; see stderr_tail. Fewer threads may avoid it."
        else:
            message = f"htrdr {result.status} (exit code {result.returncode}); see stderr_tail."
        return RenderResult(
            render_id=render_id, scene_id=scene_id, spp=spp, status=result.status,
            wall_s=round(result.wall_s, 3), predicted_wall_s=round(predicted, 3) if predicted is not None else None,
            command_line=rec.command_line, output=str(output), message=message,
            stderr_tail=[] if result.ok else result.stderr_tail[-10:], retries=len(attempts),
        )

    @mcp.tool(annotations={"readOnlyHint": True})
    def read_stats(
        render_id: str,
        target_rel_err: float | None = None,
        percentile: int = 95,
        max_spp: int | None = None,
        budget_s: float | None = None,
    ) -> dict:
        """Error and cost summaries of a finished render, never its pixels.

        The error is the per-pixel relative error σ/|μ| of Y (CIE XYZ) or the radiance (lw, sw):
        its percentiles, the share of pixels within the target, and the worst image tiles.
        With target_rel_err, also proposes the next render: the spp at which the given
        percentile reaches the target (σ ∝ 1/√spp), its predicted wall time from this scene's
        renders so far, and whether it fits budget_s and max_spp. Proposals grow spp at most
        16x per step, because noisy renders overstate high percentiles.
        """
        rec = render_or_error(render_id)
        if rec.status != "ok":
            raise ToolError(f"render {render_id} is {rec.status}; only finished renders have stats")
        if percentile not in PERCENTILES:
            raise ToolError(f"percentile must be one of {list(PERCENTILES)}")
        if target_rel_err is not None and not 0 < target_rel_err < 1:
            raise ToolError("target_rel_err is a fraction, e.g. 0.01 for 1 %")
        scene = scene_or_error(rec.scene_id)
        try:
            img = load(rec.output)
        except (OSError, HtrdrImageError) as e:
            raise ToolError(f"can't read {rec.output}: {e}") from e
        stats = compute_stats(img, spp=rec.spp, spectral=scene.spec.spectral, target_rel_err=target_rel_err)
        out: dict = {"render_id": render_id, "scene_id": rec.scene_id, **stats.to_dict()}
        model = cost_model(rec.scene_id)
        if model:
            out["cost_model"] = {"fixed_s": model.fixed_s, "per_spp_s": model.per_spp_s}
        if target_rel_err is not None and model is not None:
            try:
                out["proposal"] = propose(
                    stats, target=target_rel_err, cost=model, percentile=percentile,
                    max_spp=min(max_spp or config.max_spp, config.max_spp), budget_s=budget_s,
                ).to_dict()
            except ValueError as e:
                out["proposal"] = None
                out["notes"] = [*out["notes"], f"No proposal: {e}."]
        store.save_render(rec.model_copy(update={"stats": out}))
        return out

    @mcp.tool
    async def postprocess(
        render_id: str,
        mode: Literal["auto", "image", "map"] = "auto",
        exposure: float | None = None,
        white: float | None = None,
        component: int | None = None,
        palette: str = "inferno",
        range_min: float | None = None,
        range_max: float | None = None,
    ) -> dict:
        """Turn a render into a PNG with htpp, for people and the image viewer.

        image mode tone-maps CIE XYZ (htpp -i, with optional exposure and white);
        map mode colours one of the 8 values per pixel (htpp -m: component 0-7, palette,
        optional range). auto picks image for CIE XYZ and a map of the radiance (component 2)
        otherwise. Returns the PNG path and htpp's exact command line.
        """
        if config.htpp is None:
            raise ToolError("htpp isn't installed on this server")
        rec = render_or_error(render_id)
        if rec.status != "ok":
            raise ToolError(f"render {render_id} is {rec.status}; nothing to postprocess")
        scene = scene_or_error(rec.scene_id)
        spectral = scene.spec.spectral
        notes: list[str] = []
        if mode == "auto":
            mode = "image" if spectral.mode == "cie_xyz" else "map"
        if mode == "image":
            if spectral.mode != "cie_xyz":
                raise ToolError("image mode needs a CIE XYZ render; use map mode")
            opts = [f"exposure={exposure}"] if exposure is not None else []
            opts += [f"white={white}"] if white is not None else []
            htpp_args = ["-i", ":".join(opts) or "default"]
        else:
            comp = 2 if component is None else component
            if not 0 <= comp <= 7:
                raise ToolError("component is 0-7: the mean and σ of four pairs")
            if spectral.mode == "lw" and spectral.monochromatic and comp in (0, 1):
                notes.append("htrdr 0.12's monochromatic brightness temperature (components 0-1) is wrong; prefer the radiance (2).")
            opts = [f"pixcpnt={comp}", f"palette={palette}"]
            if (range_min is None) != (range_max is None):
                raise ToolError("give both range_min and range_max, or neither")
            if range_min is not None and range_max is not None:
                if not range_min < range_max:
                    raise ToolError("range_min must be below range_max")
                opts.append(f"range={range_min},{range_max}")
            htpp_args = ["-m", ":".join(opts)]
        out_dir = store.render_dir(render_id)
        ppm, png = out_dir / "image.ppm", out_dir / "image.png"
        argv = [*config.htpp, "-f", *htpp_args, "-o", str(ppm), rec.output]
        result = await run(argv, timeout_s=600, env=config.env)
        if not result.ok:
            raise ToolError(f"htpp {result.status}: {' / '.join(result.stderr_tail[-5:])}")
        width, height = ppm_to_png(ppm, png)
        out = {"render_id": render_id, "png": str(png), "ppm": str(ppm), "command_line": command_line(argv),
               "width": width, "height": height, "notes": notes}
        store.save_render(rec.model_copy(update={"postprocess": out}))
        return out

    @mcp.resource("htrdr://info", mime_type="application/json")
    def info() -> str:
        """What renders run with: htrdr's build manifest and binary hash, and htpp's version."""
        if config.install is None or config.htrdr != [str(config.install.binary)]:
            return dumps({"htrdr": " ".join(config.htrdr), "note": "not a real htrdr install (e.g. the fake)"})
        return dumps(config.install.info())

    @mcp.resource("htrdr://renders", mime_type="application/json")
    def list_renders() -> str:
        """Every render, oldest first: id, scene, spp, status and wall time."""
        return dumps([
            {k: getattr(r, k) for k in ("render_id", "scene_id", "spp", "status", "wall_s", "started_at")}
            for r in store.renders()
        ])

    @mcp.resource("htrdr://scenes/{scene_id}", mime_type="application/json")
    def scene_resource(scene_id: str) -> str:
        """A scene's spec, warnings and the ids of its renders."""
        rec = store.scene(scene_id)
        if rec is None:
            raise ResourceError(f"unknown scene {scene_id!r}")
        return dumps({**rec.model_dump(mode="json"), "renders": [r.render_id for r in store.renders(scene_id)]})

    @mcp.resource("htrdr://renders/{render_id}", mime_type="application/json")
    def render_resource(render_id: str) -> str:
        """A render's record: exact command line, status, timings, output tails, last stats."""
        rec = store.render(render_id)
        if rec is None:
            raise ResourceError(f"unknown render {render_id!r}")
        return rec.model_dump_json(indent=2)

    return mcp


def main(argv: list[str] | None = None) -> None:
    p = argparse.ArgumentParser(prog="htrdr-mcp", description="htrdr as an MCP server (stdio).")
    p.add_argument("--runs-dir", type=Path, default=Path(os.environ.get("HTRDR_RUNS", "runs")))
    p.add_argument("--prefix", type=Path, help="htrdr install prefix (default $HTRDR_PREFIX or ~/.local)")
    p.add_argument("--threads", type=int)
    p.add_argument("--fake", action="store_true", help="use the fake htrdr (no real renders)")
    args = p.parse_args(argv)
    install = HtrdrInstall(prefix=args.prefix) if args.prefix else HtrdrInstall()
    config = ServerConfig.for_install(args.runs_dir.resolve(), install, threads=args.threads)
    if args.fake:
        config.htrdr = FAKE_HTRDR
    elif not install.available():
        p.error(f"htrdr-atmosphere not found at {install.binary}; build it or pass --fake")
    create_server(config).run()


if __name__ == "__main__":
    main()
