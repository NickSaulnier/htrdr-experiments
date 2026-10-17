"""How many samples a target uncertainty needs, and how long that takes.

Monte Carlo error falls as 1/√N: a render at `spp` with relative error e
reaches error t at spp·(e/t)². The error used is a high percentile of the
per-pixel relative error, so most pixels, not just the average one, meet
the target. Wall time is modelled as fixed cost + cost per spp, fitted to
the renders made so far.

Caveat: σ/|μ̂| uses the *estimated* mean, which is noisy. Where the
per-pixel error is more than a few percent, some |μ̂| fall well below |μ|,
so high percentiles of σ/|μ̂| overstate the error (the median doesn't),
and the spp estimate is conservative: too many samples, never too few.
To keep a noisy preview from triggering one huge render, `propose` grows
the spp by at most `max_growth` per step; each step is less noisy, so its
estimate is better (progressive refinement).
"""

from __future__ import annotations

import math
from dataclasses import dataclass

from .stats import ImageStats

DEFAULT_PERCENTILE = 95
DEFAULT_SAFETY = 1.2
"""Headroom on the spp estimate: σ itself is estimated from few samples in a preview."""
DEFAULT_MAX_GROWTH = 16
"""Largest spp multiple per step. 16× halves the error twice, so one step can't overshoot by much."""


def spp_for_target(spp: int, rel_err: float, target: float, *, safety: float = DEFAULT_SAFETY) -> int:
    """Samples per pixel for `rel_err` at `spp` to fall to `target`. Never less than `spp`."""
    if spp < 1 or target <= 0:
        raise ValueError("spp must be ≥ 1 and the target > 0")
    if not math.isfinite(rel_err):
        raise ValueError("the current error isn't finite")
    if rel_err <= target:
        return spp
    return max(spp + 1, math.ceil(spp * (rel_err / target) ** 2 * safety))


@dataclass(frozen=True)
class CostModel:
    """wall_s ≈ fixed_s + per_spp_s · spp."""

    fixed_s: float
    per_spp_s: float

    @classmethod
    def fit(cls, samples: list[tuple[int, float]]) -> CostModel:
        """Least squares on (spp, wall_s) pairs.

        With a single render, all of its time is charged to the samples
        (fixed cost 0), which overestimates bigger renders: the safe side.
        """
        if not samples:
            raise ValueError("no renders to fit")
        if len({s for s, _ in samples}) == 1:
            spp = samples[0][0]
            return cls(0.0, sum(w for _, w in samples) / len(samples) / spp)
        n = len(samples)
        mx = sum(s for s, _ in samples) / n
        my = sum(w for _, w in samples) / n
        sxx = sum((s - mx) ** 2 for s, _ in samples)
        sxy = sum((s - mx) * (w - my) for s, w in samples)
        slope = max(sxy / sxx, 0.0)
        return cls(max(my - slope * mx, 0.0), slope)

    def predict(self, spp: int) -> float:
        return self.fixed_s + self.per_spp_s * spp


@dataclass(frozen=True)
class Proposal:
    spp: int
    predicted_wall_s: float
    rel_err_now: float
    percentile: int
    target_met: bool
    within_budget: bool
    capped: bool
    """True when the spp was cut down to `max_spp`; the target will then likely be missed."""
    stepped: bool
    """True when the spp was held to `max_growth` × the current spp: an intermediate render."""

    def to_dict(self) -> dict:
        return self.__dict__.copy()


def propose(
    stats: ImageStats,
    *,
    target: float,
    cost: CostModel,
    percentile: int = DEFAULT_PERCENTILE,
    max_spp: int | None = None,
    budget_s: float | None = None,
    safety: float = DEFAULT_SAFETY,
    max_growth: float | None = DEFAULT_MAX_GROWTH,
) -> Proposal:
    """The next render toward `target`, with its predicted cost.

    It reaches the target when the estimate holds and no limit applies;
    otherwise `stepped` or `capped` says why it falls short.
    """
    key = f"p{percentile}"
    if key not in stats.error.percentiles:
        raise ValueError(f"no {key} in the stats; percentiles are {list(stats.error.percentiles)}")
    rel_err = stats.error.percentiles[key]
    met = math.isfinite(rel_err) and rel_err <= target
    spp = stats.spp if met else spp_for_target(stats.spp, rel_err, target, safety=safety)
    stepped = max_growth is not None and spp > stats.spp * max_growth
    if stepped:
        spp = math.floor(stats.spp * max_growth)
    capped = max_spp is not None and spp > max_spp
    if capped:
        spp = max_spp
    wall = cost.predict(spp)
    return Proposal(
        spp=spp,
        predicted_wall_s=wall,
        rel_err_now=rel_err,
        percentile=percentile,
        target_met=met,
        within_budget=budget_s is None or wall <= budget_s,
        capped=capped,
        stepped=stepped,
    )
