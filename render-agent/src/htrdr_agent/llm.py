"""Talk to the agent's LLM: any OpenAI-compatible endpoint, by default the
local vLLM server from scripts/serve-llm.sh.

`parse_request` turns a natural-language request into a RenderRequest with
JSON-schema guided decoding, so the reply always matches the schema. The
system prompt holds the conventions (units, view angles, defaults) that the
eval set in evals/requests.jsonl checks.
"""

from __future__ import annotations

import os
import time
from dataclasses import dataclass, field

from openai import AsyncOpenAI

from .request import RenderRequest

SYSTEM_PROMPT = """\
You turn requests for atmospheric radiative transfer renders into a structured request for htrdr.
Fill in every field. Read values from the request where it gives them; otherwise use the defaults
below and add one short entry to `assumptions` for each value you chose.

Light and wavelength (report what the request says; code decides the rendering mode from it):
- spectral_words: the kind of light named in words. visible: "visible", "colour", "photo", "picture",
  "image of the sky", "what the eye sees". infrared: "infrared", "IR", "thermal", "brightness temperature".
  solar: "solar", "shortwave", "sunlight". none: no such word.
- Words about the sun's position or the time of day ("sunset", "noon", "sun at 40°") are not light
  words: they only set the sun.
- wavelength_min_um / wavelength_max_um: only numbers the request gives, in micrometres
  (10000 nm = 10 µm, 550 nm = 0.55 µm). A single wavelength ("10 µm") gives min = max; a band
  ("8 to 12 µm") gives its bounds. No number: both null. Don't add an assumption for these.

Sun:
- Elevation in degrees above the horizon. Sunrise or sunset: 3. Low sun: 15. Midday or noon: 60.
- Azimuth in degrees; 180 if not given. No sun mentioned: both null.
- Keep a sun the request names even for thermal infrared (code will warn that it has no effect there).

Camera:
- Altitude in metres: "2 km" = 2000, feet × 0.3048 ("10,000 ft" = 3048), "at ground level" or
  "from the ground" = 2. A flight level is hundreds of feet: FLnnn = nnn × 100 ft, so FL350 = 35,000 ft
  = 10668 m and FL200 = 6096 m. No altitude given: 1000.
- view_elevation_deg: "looking down" or "nadir" = -90, "toward the horizon" = 0, "looking up" or "zenith" = 90,
  "30° below the horizon" = -30. No direction given: 0 (the horizon), since the default scene has no ground.
- view_azimuth_deg: heading in degrees; 0 if not given.
- fov_deg: 60 unless given. "wide" = 90, "narrow" or "zoomed in" = 20.

Image:
- 320 x 240 unless given. "HD" = 1280 x 720, "full HD" = 1920 x 1080, "4K" = 3840 x 2160,
  "small" or "thumbnail" = 160 x 120, "square N" = N x N.

Quality and time:
- target_rel_err_percent: only an uncertainty given as a number, as a percentage ("1%" = 1,
  "0.5 percent" = 0.5). No number: null.
- quality_words: preview for "quick", "fast", "rough", "draft", "preview"; high_quality for
  "high quality", "best", "clean", "publication"; otherwise none.
- time_budget_s: a stated time limit in seconds ("under a minute" = 60, "5 minutes" = 300). Not stated: null.

is_render_request: true whenever the message asks for a render, an image or a view, however vague:
missing details are what the defaults are for ("Render something" is a render request). False for
questions, thanks, greetings and anything else; then put a short reply or question in clarification.
clarification: null for render requests.
"""


@dataclass(frozen=True)
class LLMConfig:
    base_url: str = field(default_factory=lambda: os.environ.get("HTRDR_LLM_URL", "http://127.0.0.1:8000/v1"))
    model: str = field(default_factory=lambda: os.environ.get("HTRDR_LLM_MODEL", "qwen"))
    api_key: str = field(default_factory=lambda: os.environ.get("HTRDR_LLM_KEY", "EMPTY"))
    temperature: float = 0.0
    thinking: bool = False
    """Let Qwen reason before answering (slower; the eval decides whether it helps)."""
    timeout_s: float = 120.0
    max_tokens: int = 4096

    def client(self) -> AsyncOpenAI:
        return AsyncOpenAI(base_url=self.base_url, api_key=self.api_key, timeout=self.timeout_s)


@dataclass(frozen=True)
class ParseResult:
    request: RenderRequest
    raw: str
    latency_s: float
    prompt_tokens: int | None
    completion_tokens: int | None


def response_format() -> dict:
    return {
        "type": "json_schema",
        "json_schema": {"name": "RenderRequest", "schema": RenderRequest.model_json_schema(), "strict": True},
    }


async def parse_request(text: str, config: LLMConfig | None = None, client: AsyncOpenAI | None = None) -> ParseResult:
    """Read a natural-language request into a RenderRequest."""
    config = config or LLMConfig()
    client = client or config.client()
    started = time.monotonic()
    completion = await client.chat.completions.create(
        model=config.model,
        messages=[{"role": "system", "content": SYSTEM_PROMPT}, {"role": "user", "content": text}],
        temperature=config.temperature,
        max_tokens=config.max_tokens,
        response_format=response_format(),  # type: ignore[arg-type]
        extra_body={"chat_template_kwargs": {"enable_thinking": config.thinking}},
    )
    raw = completion.choices[0].message.content or ""
    usage = completion.usage
    return ParseResult(
        request=RenderRequest.model_validate_json(raw),
        raw=raw,
        latency_s=time.monotonic() - started,
        prompt_tokens=usage.prompt_tokens if usage else None,
        completion_tokens=usage.completion_tokens if usage else None,
    )
