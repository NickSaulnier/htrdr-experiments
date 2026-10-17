"""The request schema, its mapping to SceneSpec, and the LLM call (with a stub endpoint)."""

import asyncio
import json
import math
from pathlib import Path

import httpx2 as httpx  # the HTTP library openai 3 depends on
import pytest
from openai import AsyncOpenAI

from htrdr_agent.evaluate import check
from htrdr_agent.llm import SYSTEM_PROMPT, LLMConfig, parse_request
from htrdr_agent.request import RenderRequest, RequestError, camera_for, resolve_spectral, target_rel_err, to_scene_spec
from htrdr_agent.scene import Inputs


def request(**over) -> RenderRequest:
    base = dict(
        spectral_words="infrared", wavelength_min_um=10.0, wavelength_max_um=10.0,
        sun_elevation_deg=40.0, sun_azimuth_deg=None, camera_altitude_m=2000.0,
        view_elevation_deg=0.0, view_azimuth_deg=0.0, fov_deg=60.0, image_width=160, image_height=120,
        target_rel_err_percent=1.0, quality_words="none", time_budget_s=None, assumptions=["view toward the horizon"],
        is_render_request=True, clarification=None,
    )
    return RenderRequest(**{**base, **over})


@pytest.fixture
def inputs(gas: Path) -> Inputs:
    return Inputs(atmosphere=gas)


def test_the_plan_example_becomes_the_m2_scene(inputs: Inputs):
    spec = to_scene_spec(request(), inputs)
    assert spec.spectral.mode == "lw" and spec.spectral.range_nm == (10000, 10000)
    assert spec.sun is not None and (spec.sun.elevation_deg, spec.sun.azimuth_deg) == (40, 180)
    assert spec.sensor.pos == (0, 0, 2000) and spec.sensor.tgt == (1000, 0, 2000) and spec.sensor.up == (0, 0, 1)
    assert spec.sensor.fov_deg == 60 and (spec.image.width, spec.image.height) == (160, 120)
    # The scene's own warnings still flag the sun in longwave mode.
    assert any("no effect in lw" in w for w in spec.warnings())


@pytest.mark.parametrize(
    ("elevation", "azimuth", "tgt"),
    [
        (0, 0, (1000, 0, 2000)),
        (0, 90, (0, 1000, 2000)),
        (-30, 0, (1000 * math.cos(math.radians(30)), 0, 2000 - 500)),
        (45, 180, (-1000 * math.cos(math.radians(45)), 0, 2000 + 1000 * math.sin(math.radians(45)))),
    ],
)
def test_view_angles_become_a_target(elevation, azimuth, tgt):
    cam = camera_for(request(view_elevation_deg=elevation, view_azimuth_deg=azimuth))
    assert cam.tgt == pytest.approx(tgt, abs=1e-3)
    assert cam.view_elevation_deg == pytest.approx(elevation)


@pytest.mark.parametrize(("elevation", "azimuth", "up"), [(-90, 0, (1, 0, 0)), (90, 90, (0, 1, 0)), (-89.5, 0, (1, 0, 0))])
def test_vertical_views_use_the_heading_as_up(elevation, azimuth, up):
    cam = camera_for(request(view_elevation_deg=elevation, view_azimuth_deg=azimuth))
    assert cam.up == pytest.approx(up, abs=1e-6)  # a valid frame: SceneSpec would reject up ∥ view


def test_spectral_modes_and_units(inputs: Inputs):
    assert to_scene_spec(request(spectral_words="visible", wavelength_min_um=None, wavelength_max_um=None), inputs).spectral.mode == "cie_xyz"
    sw = to_scene_spec(request(spectral_words="solar", wavelength_min_um=0.55, wavelength_max_um=0.55), inputs).spectral
    assert sw.mode == "sw" and sw.range_nm == (550, 550)
    band = to_scene_spec(request(wavelength_min_um=12, wavelength_max_um=8), inputs).spectral
    assert band.range_nm == (8000, 12000)  # reversed bounds are put in order
    one_bound = to_scene_spec(request(wavelength_min_um=None, wavelength_max_um=11), inputs).spectral
    assert one_bound.range_nm == (11000, 11000)


@pytest.mark.parametrize(
    ("over", "message"),
    [
        ({"wavelength_min_um": 3, "wavelength_max_um": 5}, "straddles 4"),
        ({"sun_elevation_deg": -5}, "sun elevation"),
        ({"view_elevation_deg": 120}, "view elevation"),
        ({"fov_deg": 0}, "field of view"),
        ({"image_width": 0}, "invalid image size"),
        ({"is_render_request": False, "clarification": "Happy to help!"}, "not a render request: Happy to help!"),
    ],
)
def test_unbuildable_requests_say_why(inputs: Inputs, over, message):
    with pytest.raises(RequestError, match=message):
        to_scene_spec(request(**over), inputs)


@pytest.mark.parametrize(
    ("words", "lo", "hi", "mode", "band", "note"),
    [
        ("none", None, None, "visible", (None, None), None),
        ("visible", None, None, "visible", (None, None), None),
        ("infrared", None, None, "thermal_infrared", (8, 12), "window"),
        ("solar", None, None, "solar", (0.4, 0.7), "no wavelength"),
        ("infrared", 10, 10, "thermal_infrared", (10, 10), None),
        # The wavelength decides, and a mismatch with the words is noted.
        ("infrared", 3.5, 3.5, "solar", (3.5, 3.5), "sunlight only"),
        ("none", 3.9, 3.9, "solar", (3.9, 3.9), None),
        ("solar", 10, 10, "thermal_infrared", (10, 10), "thermal emission"),
        ("visible", 0.55, 0.55, "solar", (0.55, 0.55), None),
        ("infrared", 4, 4, "thermal_infrared", (4, 4), None),  # the cutoff itself is thermal
    ],
)
def test_wavelengths_decide_the_mode_and_words_only_fill_gaps(words, lo, hi, mode, band, note):
    r = resolve_spectral(request(spectral_words=words, wavelength_min_um=lo, wavelength_max_um=hi))
    assert r.mode == mode and (r.wavelength_min_um, r.wavelength_max_um) == band
    assert (note is None and not r.notes) or (note is not None and note in r.notes[0])


def test_target_is_a_fraction_with_a_default():
    assert target_rel_err(request(target_rel_err_percent=2)) == 0.02
    assert target_rel_err(request(target_rel_err_percent=None)) == 0.01


@pytest.mark.parametrize(("pct", "words", "expected"), [(None, "preview", 0.05), (None, "high_quality", 0.005), (2, "preview", 0.02), (None, "none", 0.01)])
def test_a_stated_number_beats_quality_words_which_beat_the_default(pct, words, expected):
    assert target_rel_err(request(target_rel_err_percent=pct, quality_words=words)) == pytest.approx(expected)
    with pytest.raises(RequestError):
        target_rel_err(request(target_rel_err_percent=150))


def test_schema_has_every_field_required_for_guided_decoding():
    schema = RenderRequest.model_json_schema()
    assert set(schema["required"]) == set(schema["properties"])
    assert schema["additionalProperties"] is False


def test_parse_request_sends_the_schema_and_reads_the_reply():
    sent: list[dict] = []
    reply = request().model_dump_json()

    def handler(http_request: httpx.Request) -> httpx.Response:
        sent.append(json.loads(http_request.content))
        return httpx.Response(200, json={
            "id": "x", "object": "chat.completion", "created": 0, "model": "qwen",
            "choices": [{"index": 0, "finish_reason": "stop", "message": {"role": "assistant", "content": reply}}],
            "usage": {"prompt_tokens": 900, "completion_tokens": 120, "total_tokens": 1020},
        })

    client = AsyncOpenAI(base_url="http://stub/v1", api_key="x", http_client=httpx.AsyncClient(transport=httpx.MockTransport(handler)))
    result = asyncio.run(parse_request("IR view at 10 µm", LLMConfig(model="qwen"), client=client))
    assert result.request == request()
    assert (result.prompt_tokens, result.completion_tokens) == (900, 120)
    body = sent[0]
    assert body["model"] == "qwen" and body["temperature"] == 0
    assert body["messages"] == [{"role": "system", "content": SYSTEM_PROMPT}, {"role": "user", "content": "IR view at 10 µm"}]
    assert body["response_format"]["json_schema"]["schema"]["title"] == "RenderRequest"
    assert body["chat_template_kwargs"] == {"enable_thinking": False}


@pytest.mark.parametrize(
    ("expected", "actual", "ok"),
    [
        (2000, 2000.0, True), (2000, 2001, False), ({"approx": 3048, "tol": 5}, 3050, True),
        ({"range": [0, 6]}, 3, True), ({"range": [0, 6]}, None, False), ("nonempty", [], False),
        ("nonnull", "a question", True), (None, None, True), ("visible", "solar", False),
    ],
)
def test_eval_expectations(expected, actual, ok):
    assert check(expected, actual) is ok
