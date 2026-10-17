from pathlib import Path

from htrdr_agent.runlog import agent_version, render_markdown, viewer_link, viewer_settings
from htrdr_agent.runner import file_sha256


def test_viewer_links_only_point_inside_the_served_directory(tmp_path: Path):
    root = tmp_path / "repo"
    image = root / "runs" / "renders" / "r1" / "image.txt"
    viewer = ("http://localhost:5173/", root)
    assert viewer_link(str(image), viewer, "m=image") == "http://localhost:5173/#a=/runs/renders/r1/image.txt&m=image"
    assert viewer_link(str(tmp_path / "elsewhere.txt"), viewer) is None
    assert viewer_link(str(image), None) is None
    spaced = root / "runs" / "my render" / "image.txt"
    assert viewer_link(str(spaced), viewer) == "http://localhost:5173/#a=/runs/my%20render/image.txt"


def test_ab_links_compare_two_renders(tmp_path: Path):
    viewer = ("http://localhost:5173", tmp_path)
    link = viewer_link(str(tmp_path / "a.txt"), viewer, "cmp=diff&dv=z", b=str(tmp_path / "b.txt"))
    assert link == "http://localhost:5173/#a=/a.txt&b=/b.txt&cmp=diff&dv=z"
    assert viewer_link(str(tmp_path / "a.txt"), viewer, b="/somewhere/else.txt") is None


def test_viewer_settings_suit_each_spectral_mode():
    assert viewer_settings({"spectral": {"mode": "cie_xyz", "range_nm": None}}) == "m=image"
    # Monochromatic lw maps the radiance: htrdr 0.12's Tb is wrong there.
    assert viewer_settings({"spectral": {"mode": "lw", "range_nm": [10000, 10000]}}) == "m=map&q=1&s=value"
    assert viewer_settings({"spectral": {"mode": "lw", "range_nm": [8000, 12000]}}) == "m=map&q=0&s=value"
    assert viewer_settings({"spectral": {"mode": "sw", "range_nm": [400, 700]}}) == "m=map&q=1&s=value"


def test_environment_and_reproduce_sections():
    state = {
        "run_id": "a1", "text": "IR at 10 µm", "status": "done", "message": "Target met.",
        "spec": {"spectral": {"mode": "lw", "range_nm": [10000, 10000]}},
        "renders": [{"render_id": "r1", "spp": 8, "status": "ok", "wall_s": 1.0, "command_line": "htrdr-atmosphere -i def=2x2:spp=8", "output": "/x/r1.txt"}],
        "input_sha256": {"/data/gas.txt": "ab" * 32},
        "final": {
            "image": "/x/r1.txt", "png_command": "htpp -f -m pixcpnt=2 -o out.ppm /x/r1.txt",
            "environment": {
                "htrdr": "/p/bin/htrdr-atmosphere", "htrdr_version": "0.12", "htrdr_sha256": "cd" * 32, "built_at": "2026-10-17T18:18:45Z",
                "htpp_version": "High-Tune: Post-Process 0.5.0", "components": {"rsys": {"tag": "0.15", "commit": "abc1234"}},
                "agent": {"commit": "cad5135", "local_changes": True}, "llm": {"model": "qwen", "url": "http://127.0.0.1:8000/v1"},
            },
        },
    }
    md = render_markdown(state)
    assert "## Reproduce" in md and "htrdr-atmosphere -i def=2x2:spp=8" in md and "htpp -f -m pixcpnt=2" in md
    assert "htrdr 0.12" in md and "High-Tune: Post-Process 0.5.0" in md and "rsys 0.15 (abc1234)" in md
    assert "commit `cad5135` with local changes" in md and "`qwen`" in md
    assert f"input `/data/gas.txt`: SHA-256 `{'ab' * 32}`" in md


def test_file_hashes_and_agent_version(tmp_path: Path):
    f = tmp_path / "x.bin"
    f.write_bytes(b"htrdr")
    import hashlib
    assert file_sha256(f) == hashlib.sha256(b"htrdr").hexdigest()
    assert "commit" in agent_version()
