"""The replay viewer's payload and page (ADR-0021)."""

import hashlib
import json
import re
import shutil
import subprocess
from pathlib import Path

import pytest

from replay_sim import simulate_frames
from proj7k.engine.skills import SKILLS
from proj7k.field import trace_beatmap
from proj7k.parser import parse_osu_7k
from proj7k.profiler.cli import main, run_ingestion
from proj7k.profiler.osr import OSRReplay, serialize_osr
from proj7k.profiler.replay_view import (
    JUDGMENT_ORDER, PAYLOAD_MARK, build_replay_view, find_audio, render_replay_html, write_replay_view,
)
from proj7k.profiler.response import JUDGMENT_LOSS


@pytest.fixture(scope="module")
def played(tmp_path_factory, benchmark_manifest, benchmark_corpus):
    """A simulated player at 90% of a Regular Stream 4th chart's level, as an .osr and an .osu on disk."""
    folder = tmp_path_factory.mktemp("play")
    content = benchmark_corpus[int(benchmark_manifest["Regular Stream"]["4th"]["id"])]
    osu = folder / "chart.osu"
    osu.write_text(content, encoding="utf-8")
    beatmap = parse_osu_7k(str(osu))
    field = trace_beatmap(beatmap)
    frames = simulate_frames(beatmap, field, field.total_D * 0.9, seed=11)
    replay = OSRReplay(
        mode=3, game_version=20240101, beatmap_hash=hashlib.md5(osu.read_bytes()).hexdigest(), player_name="Sim",
        replay_hash="sim", timestamp_ticks=638000000000000000, action_frames=frames,
        life_bar=f"0|1.0,{int(frames[-1].time_ms)}|1.0",
    )
    osr = folder / "sim.osr"
    osr.write_bytes(serialize_osr(replay))
    return folder, osu, osr, beatmap, field


@pytest.fixture(scope="module")
def payload(played):
    folder, osu, osr, *_ = played
    return build_replay_view(run_ingestion(osr, osu))


def test_every_note_is_in_the_payload_with_what_the_player_did(played, payload):
    *_, beatmap, field = played
    notes = payload["notes"]
    assert len(notes) == len(beatmap.hit_objects)
    assert [n["t"] for n in notes] == sorted(n["t"] for n in notes)
    assert all(0 <= n["j"] < len(JUDGMENT_ORDER) for n in notes)
    assert all((n["o"] is None) == (n["j"] == JUDGMENT_ORDER.index(JUDGMENT_ORDER[-1])) or n["o"] is not None for n in notes)
    assert sum(1 for n in notes if n["l"] is not None) >= 0.98 * len(notes)   # each note found its demand reading
    assert any(n["e"] is not None and n["tj"] is not None for n in notes) or not any(h.end_time for h in beatmap.hit_objects)
    assert payload["summary"]["total"] == len(notes)
    assert payload["summary"]["counts"]["MISS"] == sum(1 for n in notes if n["j"] == 5)


def test_the_keystrokes_are_per_column_press_intervals(played, payload):
    assert len(payload["keys"]) == 7
    for col in payload["keys"]:
        downs = [d for d, _ in col]
        assert downs == sorted(downs)
        for d, u in col[:-1]:
            assert u is not None and u >= d
    assert sum(len(c) for c in payload["keys"]) > 0.5 * len(payload["notes"])
    assert payload["summary"]["ghost_taps"] == len(payload["ghosts"])


def test_the_timeline_conserves_what_the_field_says(played, payload):
    *_, field = played
    tl = payload["timeline"]
    n = len(tl["load"])
    assert all(len(tl[k]) == n for k in ("risk", "skill", "errors", "ghosts"))
    assert n * tl["bin_ms"] >= payload["duration_ms"] - tl["bin_ms"]
    assert sum(tl["risk"]) == pytest.approx(field.p.sum(), rel=1e-3)
    assert max(tl["load"]) == pytest.approx(field.d.max() / field.total_D, rel=1e-3)
    lost = sum(JUDGMENT_LOSS[JUDGMENT_ORDER[x["j"]]] + (JUDGMENT_LOSS[JUDGMENT_ORDER[x["tj"]]] if x["tj"] is not None else 0) for x in payload["notes"])
    assert sum(tl["errors"]) == pytest.approx(lost, abs=0.02 * n + 1)
    for h in tl["hot"]:
        assert 0 <= h["start"] < h["end"] <= payload["duration_ms"] + 9000 and 0 <= h["skill"] < len(SKILLS)
    assert [h["risk"] for h in tl["hot"]] == sorted((h["risk"] for h in tl["hot"]), reverse=True)


def test_the_radar_of_the_play_rides_along(payload):
    assert set(payload["radar"]["skills"]) == set(payload["skills"])
    assert payload["meta"]["stars"] > 0 and payload["radar"]["overall_dan"]


def test_the_page_embeds_the_payload_and_nothing_can_close_the_script(payload):
    payload = json.loads(json.dumps(payload))
    payload["meta"]["title"] = "</script><b>x"
    page = render_replay_html(payload)
    assert PAYLOAD_MARK not in page
    body = page.split("<script>", 1)[1]
    assert body.count("</script>") == 1                      # only the real one
    m = re.search(r"const P = (.*?);\nif \(!P\)", page, re.S)
    assert json.loads(m.group(1))["meta"]["title"] == "</script><b>x"


@pytest.mark.skipif(shutil.which("node") is None, reason="node is not installed")
def test_the_pages_script_is_valid_javascript(payload, tmp_path):
    page = render_replay_html(payload)
    js = tmp_path / "page.js"
    js.write_text(re.search(r"<script>(.*)</script>", page, re.S).group(1), encoding="utf-8")
    done = subprocess.run(["node", "--check", str(js)], capture_output=True, text=True)
    assert done.returncode == 0, done.stderr


def test_cli_writes_the_page_with_the_audio_referenced_relatively(played, tmp_path, capsys):
    folder, osu, osr, *_ = played
    (folder / "song.mp3").write_bytes(b"\x00")
    out = tmp_path / "out" / "view.html"
    assert main(["--replay", str(osr), "--beatmap", str(osu), "--no-save", "--view", str(out), "--audio", str(folder / "song.mp3")]) == 0
    assert "Replay viewer:" in capsys.readouterr().out
    data = json.loads(re.search(r"const P = (.*?);\nif \(!P\)", out.read_text(encoding="utf-8"), re.S).group(1))
    assert (out.parent / data["audio"]).resolve() == (folder / "song.mp3").resolve()
    assert data["sources"] == {"replay": str(osr.resolve()), "beatmap": str(osu.resolve())}
    assert find_audio(osu, "") == folder / "song.mp3"
    assert find_audio(osu, "song.mp3") == folder / "song.mp3"


def test_cli_view_without_a_path_goes_beside_the_replay(played, capsys):
    folder, osu, osr, *_ = played
    assert main(["--replay", str(osr), "--beatmap", str(osu), "--no-save", "--view", "--json"]) == 0
    assert json.loads(capsys.readouterr().out)["replay_view"] == str(osr.with_suffix(".html"))
    assert osr.with_suffix(".html").is_file()


def test_a_double_time_replay_is_laid_out_in_song_time(played, tmp_path):
    folder, osu, osr, beatmap, field = played
    from proj7k.profiler.osr import parse_osr
    rep = parse_osr(osr)
    rep.mods = 64
    dt = tmp_path / "dt.osr"
    dt.write_bytes(serialize_osr(rep))
    p = build_replay_view(run_ingestion(dt, osu))
    assert p["meta"]["rate"] == 1.5
    last = max(n["t"] for n in p["notes"])
    assert p["duration_ms"] >= last                                   # notes keep their song times
    spots = p["timeline"]["hot"]
    assert spots and all(s["end"] <= p["duration_ms"] + 8000 * 1.5 for s in spots)
    assert sum(p["timeline"]["risk"]) > 0
