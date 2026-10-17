"""Score the request parser against evals/requests.jsonl.

    htrdr-eval                       # against $HTRDR_LLM_URL (default: local vLLM)
    htrdr-eval --thinking            # let the model reason first
    htrdr-eval --repeat 3            # run every case 3 times, to see stability

Each case gives a request and expected fields. An expectation is a value
(numbers compare with a 1e-6 tolerance), {"approx": v, "tol": t},
{"range": [lo, hi]}, "nonempty", or "nonnull". Results, with every raw
reply, go to runs/evals/<timestamp>.json.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import math
import time
from dataclasses import dataclass, replace
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from .llm import LLMConfig, parse_request
from .request import DEFAULT_TARGET_PERCENT, RequestError, resolve_spectral, target_percent

EVALS = Path(__file__).resolve().parents[2] / "evals" / "requests.jsonl"


def check(expected: Any, actual: Any) -> bool:
    if expected == "nonempty":
        return bool(actual)
    if expected == "nonnull":
        return actual is not None
    if isinstance(expected, dict) and "approx" in expected:
        return isinstance(actual, (int, float)) and abs(actual - expected["approx"]) <= expected["tol"]
    if isinstance(expected, dict) and "range" in expected:
        lo, hi = expected["range"]
        return isinstance(actual, (int, float)) and lo <= actual <= hi
    if isinstance(expected, (int, float)) and not isinstance(expected, bool):
        return isinstance(actual, (int, float)) and math.isclose(actual, expected, abs_tol=1e-6)
    return expected == actual


@dataclass
class CaseResult:
    id: str
    ok: bool
    failures: dict[str, dict[str, Any]]
    latency_s: float
    raw: str
    error: str | None = None


async def run_case(case: dict, config: LLMConfig) -> CaseResult:
    try:
        result = await parse_request(case["text"], config)
    except Exception as e:  # noqa: BLE001 - a broken reply is a failed case, not a crash
        return CaseResult(case["id"], False, {}, 0.0, "", error=f"{type(e).__name__}: {e}")
    got = result.request.model_dump()
    # Spectral expectations are about the outcome, after code applies the
    # conventions (resolve_spectral), not the raw words the LLM reported.
    try:
        spectral = resolve_spectral(result.request)
    except RequestError as e:
        return CaseResult(case["id"], False, {}, result.latency_s, result.raw, error=f"RequestError: {e}")
    got |= {"spectral_mode": spectral.mode, "wavelength_min_um": spectral.wavelength_min_um, "wavelength_max_um": spectral.wavelength_max_um}
    got["target_rel_err_percent"] = target_percent(result.request) if result.request.is_render_request else None
    if case["expect"].get("target_rel_err_percent", "unset") is None:
        # "No target given" means the default applies.
        case = {**case, "expect": {**case["expect"], "target_rel_err_percent": DEFAULT_TARGET_PERCENT}}
    failures = {
        key: {"expected": exp, "got": got.get(key)} for key, exp in case["expect"].items() if not check(exp, got.get(key))
    }
    return CaseResult(case["id"], not failures, failures, result.latency_s, result.raw)


async def run_all(cases: list[dict], config: LLMConfig, repeat: int) -> list[CaseResult]:
    # vLLM batches concurrent requests, so send them together.
    return await asyncio.gather(*(run_case(c, config) for c in cases for _ in range(repeat)))


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="htrdr-eval", description=__doc__.splitlines()[0])
    p.add_argument("--cases", type=Path, default=EVALS)
    p.add_argument("--thinking", action="store_true")
    p.add_argument("--repeat", type=int, default=1)
    p.add_argument("--only", help="run only the case with this id")
    p.add_argument("--out", type=Path, default=Path("runs/evals"))
    args = p.parse_args(argv)

    cases = [json.loads(line) for line in args.cases.read_text().splitlines() if line.strip()]
    if args.only:
        cases = [c for c in cases if c["id"] == args.only]
    config = replace(LLMConfig(), thinking=args.thinking)
    started = time.monotonic()
    results = asyncio.run(run_all(cases, config, args.repeat))
    elapsed = time.monotonic() - started

    for r in results:
        mark = "ok  " if r.ok else "FAIL"
        detail = r.error or "; ".join(f"{k}: expected {v['expected']!r}, got {v['got']!r}" for k, v in r.failures.items())
        print(f"{mark} {r.id:28} {r.latency_s:5.2f}s  {detail}")
    passed = sum(r.ok for r in results)
    print(f"\n{passed}/{len(results)} passed ({config.model}, thinking={'on' if config.thinking else 'off'}) in {elapsed:.1f} s")

    args.out.mkdir(parents=True, exist_ok=True)
    out = args.out / f"{datetime.now(UTC):%Y%m%d-%H%M%S}-{'thinking' if config.thinking else 'direct'}.json"
    out.write_text(json.dumps({
        "model": config.model, "url": config.base_url, "thinking": config.thinking,
        "passed": passed, "total": len(results), "elapsed_s": elapsed,
        "results": [r.__dict__ for r in results],
    }, indent=2))
    print(f"results: {out}")
    return 0 if passed == len(results) else 1


if __name__ == "__main__":
    raise SystemExit(main())
