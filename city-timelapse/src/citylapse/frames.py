"""A day of frames: the schedule, the htrdr jobs, and a resumable runner.

    citylapse-day plan RUN --city out/city-s1 [--step 15 --start 05:00 --end 22:30 ...]
    citylapse-day calibrate RUN        # previews, then suggests spp per kind
    citylapse-day run RUN [--kinds visible,thermal] [--limit N]
    citylapse-day status RUN

`plan` writes RUN/run.json, the whole configuration. Every other command
reads it, so a run is reproducible from that one file plus its inputs.

Each job renders one frame into RUN/jobs/<id>/: frame.txt (htrdr-image) and
job.json (the exact argv, input hashes, attempts, timings and noise). A job
is done when job.json records success under the same key, a hash of the
argv and of every input file; `run` skips it then. Outputs are written to a
temporary name and renamed on success, so a killed or crashed run leaves no
partial frame behind, and rerunning `run` resumes where it stopped.

htrdr is deterministic: the same arguments give the same pixels, because
each 8x8 tile reads a fixed Threefry stream (see PLAN.md, M2). So a frame
is one htrdr run with a fixed spp per kind, retried as a whole when the
machine's SIGFPE fault kills it; a retry reproduces the same result.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
import signal
import sys
import time
from dataclasses import asdict, dataclass, field
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import numpy as np
from htrdr_agent.argv import build_argv, command_line
from htrdr_agent.image import HtrdrImage, load
from htrdr_agent.runner import HtrdrInstall, file_sha256, run_sync
from htrdr_agent.scene import ImageDef, Inputs, PerspectiveCamera, SceneSpec, Spectral, Sun

from .planck import band_brightness_temperature, band_tb_sigma
from .sun import htrdr_sun, solar_position

THERMAL_BAND = (9000.0, 10000.0)
KINDS = ("visible", "thermal")
SIGFPE = (-8, 136)  # killed by signal 8, as Python and as a shell report it


@dataclass
class DayConfig:
    latitude: float = 43.6
    longitude: float = 1.44
    date: str = "2026-06-21"
    utc_offset_h: float = 2.0  # local civil time used for the schedule and the clock
    start: str = "05:00"
    end: str = "22:30"
    step_min: int = 15
    visible_min_elevation: float = 2.0

    def times(self) -> list[datetime]:
        tz = timezone(timedelta(hours=self.utc_offset_h))
        d = date.fromisoformat(self.date)
        t = datetime.combine(d, datetime.strptime(self.start, "%H:%M").time(), tz)
        end = datetime.combine(d, datetime.strptime(self.end, "%H:%M").time(), tz)
        out = []
        while t <= end:
            out.append(t)
            t += timedelta(minutes=self.step_min)
        return out


@dataclass
class ViewConfig:
    pos: tuple[float, float, float] = (-60.0, -80.0, 110.0)
    tgt: tuple[float, float, float] = (120.0, 120.0, 0.0)
    up: tuple[float, float, float] = (0.0, 0.0, 1.0)
    fov_deg: float = 55.0
    width: int = 640
    height: int = 360


@dataclass
class RunConfig:
    city: str  # directory written by citylapse-city
    gas: str = str(Path.home() / "htrdr-data/etc/ecrad_opt_prop.txt")
    day: DayConfig = field(default_factory=DayConfig)
    view: ViewConfig = field(default_factory=ViewConfig)
    spp: dict[str, int] = field(default_factory=lambda: {"visible": 64, "thermal": 64})
    targets: dict[str, float] = field(default_factory=lambda: {"visible": 0.02, "thermal": 0.5})
    """visible: RMS sigma(Y) relative to the frame's white; thermal: RMS sigma(Tb) in K."""
    threads: int | None = None
    max_attempts: int = 4
    timeout_s: float = 3600.0

    @classmethod
    def load(cls, path: Path) -> RunConfig:
        d = json.loads(path.read_text())
        d["day"] = DayConfig(**d["day"])
        v = d["view"]
        d["view"] = ViewConfig(**{**v, "pos": tuple(v["pos"]), "tgt": tuple(v["tgt"]), "up": tuple(v["up"])})
        return cls(**d)

    def save(self, path: Path) -> None:
        path.write_text(json.dumps(asdict(self), indent=2) + "\n")


@dataclass
class Job:
    id: str
    kind: str
    time: datetime
    spec: SceneSpec
    spp: int
    sun_elevation: float
    sun_azimuth_compass: float

    @property
    def inputs(self) -> list[Path]:
        i = self.spec.inputs
        return [p for p in (i.atmosphere, i.ground, i.materials, i.clouds, i.mie) if p is not None]


def materials_for(city: Path, t: datetime) -> Path:
    """Per-step materials (written by M3's thermal model) if present, else the city's defaults."""
    step = city / "materials" / f"{t:%H%M}.mtls"
    return step if step.is_file() else city / "city.mtls"


def build_jobs(cfg: RunConfig, kinds: tuple[str, ...] = KINDS) -> list[Job]:
    city = Path(cfg.city).resolve()
    v = cfg.view
    camera = PerspectiveCamera(pos=v.pos, tgt=v.tgt, up=v.up, fov_deg=v.fov_deg)
    jobs = []
    for t in cfg.day.times():
        pos = solar_position(t, cfg.day.latitude, cfg.day.longitude)
        inputs = Inputs(atmosphere=Path(cfg.gas), ground=city / "city.obj",
                        materials=materials_for(city, t), repeat_ground=True)
        image = ImageDef(width=v.width, height=v.height)
        for kind in kinds:
            if kind == "visible":
                if pos.elevation_deg < cfg.day.visible_min_elevation:
                    continue
                az, el = htrdr_sun(pos)
                spec = SceneSpec(inputs=inputs, spectral=Spectral(mode="cie_xyz"),
                                 sun=Sun(azimuth_deg=az, elevation_deg=el), sensor=camera, image=image)
            elif kind == "thermal":
                # Longwave is emission only: the sun direction has no effect.
                spec = SceneSpec(inputs=inputs, spectral=Spectral(mode="lw", range_nm=THERMAL_BAND),
                                 sensor=camera, image=image)
            else:
                raise ValueError(f"unknown kind {kind!r}")
            jobs.append(Job(f"{t:%H%M}-{kind}", kind, t, spec, cfg.spp[kind], pos.elevation_deg, pos.azimuth_deg))
    return jobs


# --- noise -------------------------------------------------------------------

def noise(kind: str, img: HtrdrImage) -> dict:
    """The kind's target metric and a few percentiles, from a rendered frame."""
    # Both metrics use the RMS of the per-pixel sigma, not its median: at low
    # spp most pixels haven't sampled the rare, high-contribution paths yet,
    # so their reported sigma is too small and the median jumps as the spp
    # grows (thermal median: 0.10 at 32 spp, 0.21 at 40). The RMS matches
    # the measured spread of independent renders at every spp and falls as
    # 1/sqrt(spp), so calibration can extrapolate it (PLAN.md, M2).
    if kind == "visible":
        # Noise as seen on screen: sigma(Y) relative to the frame's white,
        # taken where htpp takes its automatic white (the Y at 99.5 %), but
        # on a 3x3 box-filtered Y so that noise doesn't inflate the white.
        # Per-pixel relative error would be dominated by shadowed faces lit
        # only by skylight, a few percent of white, whose relative error
        # stays high long after their noise is invisible.
        y, s = img.mean(1), img.sigma(1)
        white = float(np.percentile(_box3(y), 99.5))
        ok = y > 0
        rel = s[ok] / y[ok]
        rms = float(np.sqrt(np.mean(s**2)))
        return {"metric": "RMS sigma(Y) / white", "value": rms / white if white > 0 else math.nan,
                "median": float(np.median(s)) / white if white > 0 else math.nan,
                "white_y": white,
                "pixel_relative_median": float(np.median(rel)) if rel.size else math.nan,
                "zero_pixels": float(1 - ok.mean())}
    lo, hi = THERMAL_BAND
    tb = band_brightness_temperature(img.mean(1), lo, hi)
    sig = band_tb_sigma(tb, img.sigma(1), lo, hi)
    ok = np.isfinite(sig)
    return {"metric": "RMS sigma(Tb) in K (from the radiance; htrdr's is 2x too large)",
            "value": float(np.sqrt(np.mean(sig[ok] ** 2))) if ok.any() else math.nan,
            "median": float(np.median(sig[ok])) if ok.any() else math.nan,
            "tb_median": float(np.nanmedian(tb)),
            "tb_htrdr_max_abs_diff": float(np.nanmax(np.abs(tb - img.mean(0)))) if ok.any() else math.nan}


def _box3(a: np.ndarray) -> np.ndarray:
    """3x3 box filter with edge padding."""
    p = np.pad(a, 1, mode="edge")
    h, w = a.shape
    return sum(p[i:i + h, j:j + w] for i in range(3) for j in range(3)) / 9.0


def spp_for(kind: str, spp: int, value: float, target: float, safety: float = 1.2) -> int:
    """Both metrics fall as 1/sqrt(spp)."""
    return max(1, math.ceil(spp * (value / target) ** 2 * safety))


# --- running -------------------------------------------------------------------

def job_key(job: Job, argv: list[str]) -> str:
    h = hashlib.sha256()
    h.update(json.dumps(argv[:-1]).encode())  # the output path is the last argument
    for p in job.inputs:
        h.update(f"{p}:{file_sha256(p)}".encode())
    return h.hexdigest()[:20]


def is_done(job_dir: Path, key: str) -> bool:
    meta = job_dir / "job.json"
    if not (meta.is_file() and (job_dir / "frame.txt").is_file()):
        return False
    try:
        d = json.loads(meta.read_text())
    except json.JSONDecodeError:
        return False
    return d.get("status") == "ok" and d.get("key") == key


def render(job: Job, out_dir: Path, cfg: RunConfig, install: HtrdrInstall, *,
           spp: int | None = None, name: str = "frame.txt", log=print) -> dict:
    """Render one job into out_dir/name, retrying SIGFPE; return its record."""
    out_dir.mkdir(parents=True, exist_ok=True)
    spp = spp or job.spp
    final = out_dir / name
    partial = out_dir / (name + ".partial")
    threads = cfg.threads or os.cpu_count() or 1
    attempts = []
    record: dict = {"id": job.id, "kind": job.kind, "time": job.time.isoformat(), "spp": spp,
                    "sun": {"elevation_deg": job.sun_elevation, "azimuth_compass_deg": job.sun_azimuth_compass},
                    "inputs": {str(p): file_sha256(p) for p in job.inputs}}
    for attempt in range(1, cfg.max_attempts + 1):
        argv = build_argv(job.spec, spp=spp, output=partial, binary=install.binary, threads=threads)
        partial.unlink(missing_ok=True)
        r = run_sync(argv, env=install.env(), timeout_s=cfg.timeout_s)
        attempts.append({"threads": threads, "status": r.status, "returncode": r.returncode, "wall_s": round(r.wall_s, 3)})
        if r.ok and partial.is_file() and partial.stat().st_size > 0:
            partial.replace(final)
            record.update(status="ok", argv=argv[:-1] + [str(final)],
                          command_line=command_line(argv[:-1] + [str(final)]), attempts=attempts)
            return record
        log(f"  {job.id}: attempt {attempt} {r.status} (returncode {r.returncode}, {r.wall_s:.1f} s)")
        if r.returncode not in SIGFPE:
            record.update(status="failed", stderr_tail=r.stderr_tail[-10:])
            break
        threads = max(1, threads // 2)  # what the MCP server does: lighter load, fewer faults
    partial.unlink(missing_ok=True)
    record.setdefault("status", "failed")
    record.update(attempts=attempts, argv=argv[:-1] + [str(final)])
    return record


def run_jobs(run_dir: Path, cfg: RunConfig, jobs: list[Job], *, limit: int | None = None, log=print) -> dict:
    install = HtrdrInstall()
    counts = {"done": 0, "rendered": 0, "failed": 0}
    for job in jobs:
        jd = run_dir / "jobs" / job.id
        jd.mkdir(parents=True, exist_ok=True)
        for stray in jd.glob("*.partial"):
            stray.unlink()
        argv = build_argv(job.spec, spp=job.spp, output=jd / "frame.txt", binary=install.binary)
        key = job_key(job, argv)
        if is_done(jd, key):
            counts["done"] += 1
            continue
        if limit is not None and counts["rendered"] + counts["failed"] >= limit:
            break
        started = time.monotonic()
        rec = render(job, jd, cfg, install, log=log)
        rec["key"] = key
        if rec["status"] == "ok":
            rec["noise"] = noise(job.kind, load(jd / "frame.txt"))
            counts["rendered"] += 1
            log(f"{job.id}: ok in {time.monotonic() - started:.1f} s, {rec['noise']['metric']} = {rec['noise']['value']:.4g}"
                + (f" after {len(rec['attempts'])} attempts" if len(rec["attempts"]) > 1 else ""))
        else:
            counts["failed"] += 1
            log(f"{job.id}: FAILED after {len(rec['attempts'])} attempts")
        tmp = jd / "job.json.partial"
        tmp.write_text(json.dumps(rec, indent=2) + "\n")
        tmp.replace(jd / "job.json")
    return counts


def calibrate(run_dir: Path, cfg: RunConfig, *, preview_spp: int = 32, log=print) -> dict:
    """Render previews at a few times per kind and suggest the spp that meets each target everywhere."""
    install = HtrdrInstall()
    jobs = build_jobs(cfg)
    out: dict = {}
    for kind in KINDS:
        ks = [j for j in jobs if j.kind == kind]
        if not ks:
            continue
        # The lowest sun (noisiest visible frame), noon, and the extremes of the day.
        picks = {ks[0].id, ks[-1].id, ks[len(ks) // 2].id, min(ks, key=lambda j: j.sun_elevation).id}
        probes = []
        for job in [j for j in ks if j.id in picks]:
            rec = render(job, run_dir / "calibration", cfg, install, spp=preview_spp, name=f"{job.id}.txt", log=log)
            if rec["status"] != "ok":
                log(f"calibration {job.id} failed")
                continue
            n = noise(kind, load(run_dir / "calibration" / f"{job.id}.txt"))
            wall = rec["attempts"][-1]["wall_s"]
            need = spp_for(kind, preview_spp, n["value"], cfg.targets[kind])
            probes.append({"id": job.id, "sun_elevation": round(job.sun_elevation, 2), "spp": preview_spp,
                           "value": n["value"], "wall_s": wall, "spp_needed": need})
            log(f"calibration {job.id}: {n['metric']} = {n['value']:.4g} at {preview_spp} spp -> {need} spp")
        if probes:
            need = max(p["spp_needed"] for p in probes)
            per_spp = max(p["wall_s"] / p["spp"] for p in probes)  # all time charged to samples: the safe side
            out[kind] = {"target": cfg.targets[kind], "suggested_spp": need, "probes": probes,
                         "predicted_wall_s_per_frame": round(per_spp * need, 1),
                         "frames": len(ks), "predicted_total_h": round(per_spp * need * len(ks) / 3600, 2)}
    (run_dir / "calibration.json").write_text(json.dumps(out, indent=2) + "\n")
    return out


def status(run_dir: Path, cfg: RunConfig) -> dict:
    jobs = build_jobs(cfg)
    done = failed = 0
    wall = 0.0
    for j in jobs:
        meta = run_dir / "jobs" / j.id / "job.json"
        if meta.is_file():
            d = json.loads(meta.read_text())
            if d.get("status") == "ok":
                done += 1
                wall += sum(a["wall_s"] for a in d["attempts"])
            else:
                failed += 1
    return {"jobs": len(jobs), "done": done, "failed": failed, "pending": len(jobs) - done - failed,
            "render_hours": round(wall / 3600, 3)}


def main(argv: list[str] | None = None) -> None:
    ap = argparse.ArgumentParser(description="Render a day of city frames with htrdr-atmosphere.")
    sub = ap.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("plan", help="write RUN/run.json")
    p.add_argument("run", type=Path)
    p.add_argument("--city", type=Path, required=True)
    p.add_argument("--start", default="05:00")
    p.add_argument("--end", default="22:30")
    p.add_argument("--step", type=int, default=15, help="minutes between frames")
    p.add_argument("--date", default="2026-06-21")
    p.add_argument("--size", default="640x360")
    p.add_argument("--visible-spp", type=int, default=64)
    p.add_argument("--thermal-spp", type=int, default=64)
    p.add_argument("--force", action="store_true", help="overwrite an existing run.json")
    for name in ("calibrate", "run", "status"):
        q = sub.add_parser(name)
        q.add_argument("run", type=Path)
        if name == "run":
            q.add_argument("--kinds", default=",".join(KINDS))
            q.add_argument("--limit", type=int, help="render at most N frames this time")
        if name == "calibrate":
            q.add_argument("--preview-spp", type=int, default=32)
    args = ap.parse_args(argv)

    if args.cmd == "plan":
        path = args.run / "run.json"
        if path.exists() and not args.force:
            sys.exit(f"{path} exists; pass --force to replace it (finished frames whose inputs and argv still match are kept)")
        w, h = (int(x) for x in args.size.split("x"))
        cfg = RunConfig(city=str(args.city.resolve()),
                        day=DayConfig(date=args.date, start=args.start, end=args.end, step_min=args.step),
                        view=ViewConfig(width=w, height=h),
                        spp={"visible": args.visible_spp, "thermal": args.thermal_spp})
        args.run.mkdir(parents=True, exist_ok=True)
        cfg.save(path)
        jobs = build_jobs(cfg)
        print(f"{len(jobs)} jobs: " + ", ".join(f"{sum(j.kind == k for j in jobs)} {k}" for k in KINDS))
        return

    cfg = RunConfig.load(args.run / "run.json")

    # htrdr runs in its own process group, so a plain SIGTERM to this process
    # would leave it rendering, orphaned. Treat SIGTERM like Ctrl-C: the
    # runner then stops htrdr's whole group before exiting.
    def on_term(signum, frame):
        raise KeyboardInterrupt
    signal.signal(signal.SIGTERM, on_term)

    try:
        if args.cmd == "calibrate":
            print(json.dumps(calibrate(args.run, cfg, preview_spp=args.preview_spp), indent=2))
        elif args.cmd == "run":
            kinds = tuple(k for k in args.kinds.split(",") if k)
            print(json.dumps(run_jobs(args.run, cfg, build_jobs(cfg, kinds), limit=args.limit)))
        else:
            print(json.dumps(status(args.run, cfg), indent=2))
    except KeyboardInterrupt:
        # htrdr has been stopped by then; its partial output is cleaned up on the next run.
        print("interrupted; run again to resume", file=sys.stderr)
        sys.exit(130)


if __name__ == "__main__":
    main()
