"""Scenes and renders on disk, so they outlive the server process.

    <runs>/scenes/<scene_id>/scene.json
    <runs>/renders/<render_id>/render.json    the render's record: exact command
                                               line, status, timings, output tails
    <runs>/renders/<render_id>/image.txt      htrdr's output
    <runs>/renders/<render_id>/image.png      after postprocess
    <runs>/cache/<cache_key>.cache            htrdr's -O acceleration structures

A scene's id is a hash of its spec, so building the same scene twice is
idempotent. Records are written atomically (temp file, then rename).
"""

from __future__ import annotations

import hashlib
import itertools
import json
import os
import threading
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel

from .scene import SceneSpec

RenderStatus = Literal["running", "ok", "failed", "timeout", "cancelled"]


def now() -> str:
    return datetime.now(UTC).isoformat(timespec="milliseconds")


class SceneRecord(BaseModel):
    scene_id: str
    spec: SceneSpec
    warnings: list[str]
    cache_key: str
    created_at: str


class RenderRecord(BaseModel):
    render_id: str
    scene_id: str
    spp: int
    status: RenderStatus
    command_line: str
    argv: list[str]
    output: str
    started_at: str
    finished_at: str | None = None
    wall_s: float | None = None
    returncode: int | None = None
    stdout_tail: list[str] = []
    stderr_tail: list[str] = []
    stats: dict[str, Any] | None = None
    """The last read_stats summary, for the record."""
    postprocess: dict[str, Any] | None = None


def _write_json(path: Path, model: BaseModel) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(model.model_dump_json(indent=2))
    os.replace(tmp, path)


class Store:
    def __init__(self, root: Path | str):
        self.root = Path(root)
        self._lock = threading.Lock()
        self._seq = itertools.count(1)
        for sub in ("scenes", "renders", "cache"):
            (self.root / sub).mkdir(parents=True, exist_ok=True)

    # --- scenes -----------------------------------------------------------

    @staticmethod
    def scene_id(spec: SceneSpec) -> str:
        return "s" + hashlib.sha256(spec.model_dump_json().encode()).hexdigest()[:12]

    def put_scene(self, spec: SceneSpec) -> SceneRecord:
        sid = self.scene_id(spec)
        path = self.root / "scenes" / sid / "scene.json"
        with self._lock:
            if path.exists():
                return SceneRecord.model_validate_json(path.read_text())
            rec = SceneRecord(scene_id=sid, spec=spec, warnings=spec.warnings(), cache_key=spec.cache_key(), created_at=now())
            _write_json(path, rec)
            return rec

    def scene(self, scene_id: str) -> SceneRecord | None:
        path = self.root / "scenes" / scene_id / "scene.json"
        if not _safe_id(scene_id) or not path.is_file():
            return None
        return SceneRecord.model_validate_json(path.read_text())

    def cache_path(self, scene: SceneRecord) -> Path:
        return self.root / "cache" / f"{scene.cache_key}.cache"

    # --- renders ----------------------------------------------------------

    def new_render_id(self, scene_id: str, spp: int) -> str:
        stamp = datetime.now(UTC).strftime("%Y%m%d-%H%M%S")
        while True:
            rid = f"r{stamp}-{next(self._seq):03d}-{scene_id[1:7]}-{spp}"
            if not (self.root / "renders" / rid).exists():
                return rid

    def render_dir(self, render_id: str) -> Path:
        return self.root / "renders" / render_id

    def save_render(self, rec: RenderRecord) -> None:
        with self._lock:
            _write_json(self.render_dir(rec.render_id) / "render.json", rec)

    def render(self, render_id: str) -> RenderRecord | None:
        path = self.render_dir(render_id) / "render.json"
        if not _safe_id(render_id) or not path.is_file():
            return None
        return RenderRecord.model_validate_json(path.read_text())

    def renders(self, scene_id: str | None = None) -> list[RenderRecord]:
        """All renders, oldest first, optionally for one scene."""
        out = []
        for path in sorted((self.root / "renders").glob("*/render.json")):
            rec = RenderRecord.model_validate_json(path.read_text())
            if scene_id is None or rec.scene_id == scene_id:
                out.append(rec)
        return sorted(out, key=lambda r: r.started_at)


def _safe_id(text: str) -> bool:
    """Ids come from clients: never let one name a path outside the store."""
    return bool(text) and all(c.isalnum() or c == "-" for c in text)


def dumps(data: Any) -> str:
    return json.dumps(data, indent=2, default=str)
