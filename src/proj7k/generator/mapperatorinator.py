"""
Notes written by Mapperatorinator, an audio-to-chart transformer, and brought to the target star rating
by the engine (ADR-0040).

Mapperatorinator (OliBomby, MIT) is a Whisper-style model: the encoder reads the song's mel spectrogram,
the decoder writes the chart as a sequence of events (time, snap, column, hold note start and end),
16 seconds of audio at a time. It was trained on every ranked osu! chart of all four modes, so asked for
`gamemode=3, keycount=7` it writes 7K. Rhythm and keys come out of one model that hears the music
around them, which is what the pipeline in `generate` lacks (ADR-0040).

It needs Python 3.10 and PyTorch, and proj7k runs on 3.14, so it lives in an environment of its own:
`install` clones the pinned commit under `~/.cache/proj7k/mapperatorinator/repo` and makes a
`.venv` there with uv (which also fetches Python 3.10 and the PyTorch build for the machine's GPU);
`run_model` calls its `inference.py` in a subprocess. The weights (about 1 GB) download from Hugging
Face on the first run.

Its difficulty condition is osu!'s own star rating, not the engine's, so `generate_from_file` runs the
model a few times: each run is scored by the engine and the next run's difficulty is scaled by
target / stars, until a run lands within the tolerance or the runs are spent; the closest run is kept.
"""

import copy
import logging
import os
import shutil
import subprocess
import sys
import time
import wave
from pathlib import Path
from typing import Callable, List, Optional, Sequence, Union

import numpy as np

from proj7k.engine import evaluate_notes
from proj7k.generator import legal
from proj7k.generator.audio import AudioDecodeError
from proj7k.i18n import tr
from proj7k.parser import Beatmap7K, NoteType, dump_osu_7k, parse_osu_7k

logger = logging.getLogger(__name__)

REPO_URL = "https://github.com/OliBomby/Mapperatorinator.git"
#: The commit installed and tested (main, 2026-08-13); the model weights are named by `CONFIG`.
COMMIT = "0e2b0e387aab4b35c64b0b11b12d47578dea7587"
CONFIG = "v32"
PYTHON = "3.10"
HOME = Path(os.environ.get("PROJ7K_MAPPERATORINATOR", Path.home() / ".cache" / "proj7k" / "mapperatorinator"))
#: Requirements only its training or desktop UI uses; leaving them out keeps the install smaller.
SKIP_REQUIREMENTS = ("pywebview", "pyqtwebengine")
#: Its difficulty token covers 0–12 stars in 24 classes.
MIN_DIFFICULTY, MAX_DIFFICULTY = 0.5, 12.0
#: How far one run may move the next run's difficulty (×), so one odd run can't throw the search off.
MAX_STEP = 1.6
DEFAULT_RUNS = 4
#: The first run asks for this much more than the target: the model tends to land under the engine's stars (10
#: charts, 2026-10-23: 8 under, median 0.56★ off), and a chart a little too hard is brought down by deleting notes,
#: which is one run instead of several.
AIM = 1.15
#: A run at most this far above the target is brought down to it by the downscaler (pure deletion, technique kept).
TRIM_MAX = 1.5
#: Deleting notes moves the stars in steps, so a trimmed run this close to the target is kept and the loop stops
#: (meow, 2026-10-23: trims landed 0.14–0.17★ off and each miss cost another ~50 s run).
TRIM_TOLERANCE = 0.2
#: A stretch of at least this many beats with no note is a hole...
HOLE_BEATS = 4
#: ...and a run whose holes cover this much more of the chart than the reference's (or of the chart, with no
#: reference) is passed over while another run has fewer. Its own timing at difficulty 12 left 29% of 9Lana's
#: Propose empty, in different places on each seed (meow, 2026-10-24); a human chart's breaks are a few percent.
HOLE_MAX = 0.10
#: A stretch this many beats long with no note, where the reference chart has notes (or anywhere, with no
#: reference), is written again on its own, the model reading the notes around it. In fast mode the windows of an
#: outro can come back with nothing at all (the nightcore song, 171.9–196.5 s, at full loudness; meow, 2026-10-24).
REFILL_BEATS = 8
#: At most this many stretches per run, longest first; each is one more model call (~16 s to load, then the stretch).
REFILL_MAX = 3
DEFAULT_YEAR = 2023
#: Fast mode writes overlapping windows and keeps this much of each. A window writes at most 2560 tokens, about 400
#: notes: dense 7K used them up 10.9–13.3 s into its 16.4 s and left the rest empty (meow, 2026-10-24); half a
#: window holds up to ~49 notes a second.
FAST_KEEP = 0.5

#: (audio, output dir, difficulty, seed, extra overrides) -> the .osu it wrote. Tests stand in for the model.
Runner = Callable[[Path, Path, float, int, Sequence[str]], Path]


class ModelUnavailable(RuntimeError):
    """Mapperatorinator is not installed (and installing it was not asked for, or failed)."""


def venv_python(home: Optional[Path] = None) -> Path:
    venv = (home or HOME) / ".venv"
    return venv / "Scripts" / "python.exe" if os.name == "nt" else venv / "bin" / "python"


def repo_dir(home: Optional[Path] = None) -> Path:
    return (home or HOME) / "repo"


def python_dir(home: Optional[Path] = None) -> Path:
    """Where uv unpacks the model's Python. Not uv's default under %APPDATA%: when the install runs inside an MSIX
    app (the Claude desktop app), Windows redirects those writes into the app's private LocalCache, and the dashboard,
    started outside it, gets "No Python at '"…\\uv\\python\\cpython-3.10…\\python.exe'" from the venv launcher."""
    return (home or HOME) / "python"


def base_python(home: Optional[Path] = None) -> Optional[Path]:
    """The interpreter the venv runs on: its pyvenv.cfg `home`."""
    try:
        text = ((home or HOME) / ".venv" / "pyvenv.cfg").read_text(encoding="utf-8")
    except OSError:
        return None
    for line in text.splitlines():
        key, _, value = line.partition("=")
        if key.strip().lower() == "home":
            return Path(value.strip()) / ("python.exe" if os.name == "nt" else "python3")
    return None


def available(home: Optional[Path] = None) -> bool:
    base = base_python(home)
    return venv_python(home).is_file() and base is not None and base.is_file() and \
        (repo_dir(home) / "inference.py").is_file() and (home or HOME).joinpath(".installed").is_file()


def requirements(text: str) -> List[str]:
    """Its requirements.txt lines worth installing (torch comes separately, for the machine's GPU)."""
    out = []
    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        name = line.split("==")[0].split("[")[0].split("@")[0].split(";")[0].strip().lower()
        if name in SKIP_REQUIREMENTS:
            continue
        out.append(line)
    return out


def _uv() -> List[str]:
    uv = shutil.which("uv")
    if uv:
        return [uv]
    proc = subprocess.run([sys.executable, "-m", "uv", "--version"], capture_output=True)
    if proc.returncode != 0:
        subprocess.run([sys.executable, "-m", "pip", "install", "uv"], check=True, capture_output=True)
    return [sys.executable, "-m", "uv"]


#: git (here and for its git requirements) may write paths past Windows' 260-character limit, e.g. slider's example beatmaps.
GIT_ENV = {"GIT_CONFIG_COUNT": "1", "GIT_CONFIG_KEY_0": "core.longpaths", "GIT_CONFIG_VALUE_0": "true"}


#: What the running interpreter (proj7k's own Python 3.14 venv) tells its children about itself; the model's Python
#: 3.10 venv has no use for them, so its processes start without them.
PARENT_PYTHON_VARS = ("__PYVENV_LAUNCHER__", "__PYVENV_BASE_EXECUTABLE__", "PYTHONHOME", "PYTHONPATH",
                      "PYTHONEXECUTABLE", "VIRTUAL_ENV", "PYTHONSTARTUP", "PYTHONNOUSERSITE")


def child_env(**extra: str) -> dict:
    """The environment for the model's own Python and its installer: the user's, without the parent interpreter's."""
    env = {k: v for k, v in os.environ.items() if k.upper() not in PARENT_PYTHON_VARS}
    env.update(extra)
    return env


def _run(cmd: Sequence[str], log: Callable[[str], None], cwd: Optional[Path] = None, **env: str) -> None:
    proc = subprocess.run(list(map(str, cmd)), cwd=cwd, capture_output=True, text=True, encoding="utf-8", errors="replace",
                          env=child_env(**GIT_ENV, **env))
    if proc.returncode != 0:
        raise ModelUnavailable(tr("安装 Mapperatorinator 失败：", "Failed to install Mapperatorinator: ")
                               + " ".join(map(str, cmd[:4])) + " … " + (proc.stderr or proc.stdout).strip()[-600:])


def _uv_env(home: Path) -> dict:
    return {"UV_PYTHON_INSTALL_DIR": str(python_dir(home))}


def _managed_python(uv: List[str], log: Callable[[str], None], home: Path) -> str:
    """The exact interpreter uv installed for `PYTHON` (under `python_dir`), not its minor-version link
    (`cpython-3.10-…`): on some Windows machines that junction can't be traversed, and `uv python install` /
    `uv venv --python 3.10` fail on it even though the interpreter itself is unpacked fine."""
    env = child_env(**_uv_env(home))
    installing = subprocess.run(uv + ["python", "install", PYTHON], capture_output=True, text=True,
                                encoding="utf-8", errors="replace", env=env)
    proc = subprocess.run(uv + ["python", "dir"], capture_output=True, text=True, encoding="utf-8", errors="replace",
                          env=env)
    root = Path(proc.stdout.strip())
    exe = "python.exe" if os.name == "nt" else "bin/python3"
    found = sorted((d for d in root.glob(f"cpython-{PYTHON}.*") if (d / exe).is_file()),
                   key=lambda d: [int(x) for x in d.name.split("-")[1].split(".") if x.isdigit()])
    if found:
        return str(found[-1] / exe)
    if installing.returncode != 0:
        _run(uv + ["python", "install", PYTHON], log, **_uv_env(home))
    return PYTHON


def _rehome(home: Path, python: Path, log: Callable[[str], None]) -> None:
    """Point an existing venv whose interpreter this process can't see (see `python_dir`) at `python`, keeping its
    packages; if there is no such interpreter, drop the venv so it is built again."""
    cfg = home / ".venv" / "pyvenv.cfg"
    if not python.is_file():
        shutil.rmtree(home / ".venv", ignore_errors=True)
        return
    log(tr("Mapperatorinator 的 Python 不可见，改用 ", "Mapperatorinator's Python can't be seen; using ") + str(python))
    lines = [f"home = {python.parent}" if line.partition("=")[0].strip().lower() == "home" else line
             for line in cfg.read_text(encoding="utf-8").splitlines()]
    cfg.write_text("\n".join(lines) + "\n", encoding="utf-8")


#: Our changes to its pinned source, (file, line it follows or replaces, new lines): `parallel_keep`, so parallel
#: sampling can write overlapping windows and keep only the start of each (`FAST_KEEP`).
PATCHES = (
    ("config.py", "    parallel: bool = False  # Use parallel sampling", [
        "    parallel: bool = False  # Use parallel sampling",
        "    parallel_keep: float = 1.0  # Fraction of each window kept in parallel sampling; the next window starts there",
    ]),
    ("osuT5/osuT5/inference/preprocessor.py", "            self.sequence_stride = self.samples_per_sequence", [
        "            self.sequence_stride = int(self.samples_per_sequence * min(max(getattr(args, 'parallel_keep', 1.0), 0.1), 1.0))",
    ]),
    ("osuT5/osuT5/inference/processor.py", "        self.parallel = args.parallel", [
        "        self.parallel = args.parallel",
        "        self.parallel_keep = min(max(getattr(args, 'parallel_keep', 1.0), 0.1), 1.0)",
    ]),
    ("osuT5/osuT5/inference/processor.py",
     "                        self.add_predicted_tokens_to_context(context, sequence[start:end], frame_time)", [
        "                        self.add_predicted_tokens_to_context(context, sequence[start:end], frame_time)",
        "                        self._keep_window(context, frame_time, sequence_index, len(frame_times))",
    ]),
    ("osuT5/osuT5/inference/processor.py",
     "                    self.add_predicted_tokens_to_context(out_context[0], sequence[start:end], frame_time)", [
        "                    self.add_predicted_tokens_to_context(out_context[0], sequence[start:end], frame_time)",
        "                    self._keep_window(out_context[0], frame_time, sequence_index, len(frame_times))",
    ]),
    ("osuT5/osuT5/inference/processor.py", "    def generate_parallel(", [
        "    def _keep_window(self, context, frame_time, index, count):",
        "        # proj7k: with overlapping windows, a window keeps what lies before the next one starts.",
        "        if self.parallel_keep < 1.0 and index != count - 1:",
        "            keep = frame_time + self.parallel_keep * self.miliseconds_per_sequence",
        "            self._trim_events_after_time(context[\"events\"], context[\"event_times\"], keep)",
        "",
        "    def generate_parallel(",
    ]),
)
PATCH_MARK = "proj7k: with overlapping windows"


def patched(home: Optional[Path] = None) -> bool:
    path = repo_dir(home) / "osuT5/osuT5/inference/processor.py"
    return path.is_file() and PATCH_MARK in path.read_text(encoding="utf-8", errors="replace")


def patch(home: Optional[Path] = None) -> None:
    """Apply `PATCHES` to the checkout (once; it keeps the file's own line endings)."""
    if patched(home):
        return
    repo = repo_dir(home)
    texts = {}
    for name, line, new in PATCHES:
        text = texts.get(name)
        if text is None:
            text = texts[name] = (repo / name).read_bytes().decode("utf-8")
        nl = "\r\n" if "\r\n" in text else "\n"
        lines = text.split(nl)
        if lines.count(line) != 1:
            raise ModelUnavailable(tr(f"Mapperatorinator 的源码和固定版本不一致，打不上补丁：{name}",
                                      f"Mapperatorinator's source doesn't match the pinned commit; can't patch {name}"))
        i = lines.index(line)
        texts[name] = nl.join(lines[:i] + new + lines[i + 1:])
    for name, text in texts.items():
        (repo / name).write_bytes(text.encode("utf-8"))


def install(log: Callable[[str], None] = logger.info, home: Optional[Path] = None) -> None:
    """Clone the pinned commit, patch it (`PATCHES`) and build its Python 3.10 environment (once; a few GB with
    PyTorch)."""
    home = home or HOME
    if available(home):
        patch(home)
        return
    log(tr("首次使用 Mapperatorinator：正在安装（Python 3.10 + PyTorch，数 GB，只需一次）……",
           "First use of Mapperatorinator: installing it (Python 3.10 + PyTorch, several GB, only once)…"))
    if shutil.which("git") is None:
        raise ModelUnavailable(tr("安装 Mapperatorinator 需要 git", "Installing Mapperatorinator needs git"))
    home.mkdir(parents=True, exist_ok=True)
    repo = repo_dir(home)
    if not (repo / ".git").is_dir():
        _run(["git", "clone", "--quiet", REPO_URL, repo], log)
    _run(["git", "-C", repo, "fetch", "--quiet", "origin", COMMIT], log)
    _run(["git", "-C", repo, "checkout", "--quiet", "--force", COMMIT], log)
    patch(home)
    uv = _uv()
    py = venv_python(home)
    base = base_python(home)
    if py.is_file() and not (base and base.is_file()):
        _rehome(home, Path(_managed_python(uv, log, home)), log)
    if not py.is_file():
        _run(uv + ["venv", "--python", _managed_python(uv, log, home), home / ".venv"], log, **_uv_env(home))
    log(tr("正在安装 PyTorch（按本机显卡选择版本）……", "Installing PyTorch (the build for this machine's GPU)…"))
    _run(uv + ["pip", "install", "--python", py, "--torch-backend", "auto", "torch", "torchaudio"], log)
    reqs = requirements((repo / "requirements.txt").read_text(encoding="utf-8"))
    log(tr("正在安装 Mapperatorinator 的其余依赖……", "Installing Mapperatorinator's other dependencies…"))
    _run(uv + ["pip", "install", "--python", py, "--torch-backend", "auto"] + reqs, log)
    (home / ".installed").write_text(COMMIT + "\n", encoding="utf-8")
    log(tr("Mapperatorinator 安装完成。", "Mapperatorinator installed."))


def quote(value: str) -> str:
    """A value as a Hydra override: quoted, so spaces, brackets and '=' in a path survive."""
    return "'" + str(value).replace("\\", "\\\\").replace("'", "\\'") + "'"


def overrides(audio: Path, output_dir: Path, difficulty: float, seed: int, *, hold_note_ratio: Optional[float] = None,
              year: int = DEFAULT_YEAR, title: str = "", artist: str = "", timing_from: Optional[Path] = None,
              fast: bool = False) -> List[str]:
    """Its inference.py arguments. It writes no scroll speed changes (we don't use them in 7K: SV costs a pass and
    changes nothing else); with `timing_from`, a chart of the same song, it reads that chart's timing instead of
    writing its own (faster, and the rows land on the chart's beats); `fast` writes all windows at once instead of
    one after another (about twice as fast, slightly fewer rows and long notes; 2026-10-23 on meow), overlapping so
    no window runs out of tokens before its end (`FAST_KEEP`)."""
    out = [
        f"audio_path={quote(Path(audio).resolve().as_posix())}",
        f"output_path={quote(Path(output_dir).resolve().as_posix())}",
        "gamemode=3", "keycount=7",
        f"difficulty={difficulty:.2f}", f"seed={int(seed)}", f"year={int(year)}",
        "export_osz=false",
    ]
    if timing_from is not None:
        out += [f"beatmap_path={quote(Path(timing_from).resolve().as_posix())}", "in_context=[TIMING]", "output_type=[MAP]"]
    else:
        out.append("output_type=[TIMING,MAP]")
    if fast:
        out += ["parallel=true", f"parallel_keep={FAST_KEEP}"]
    if hold_note_ratio is not None:
        out.append(f"hold_note_ratio={min(max(hold_note_ratio, 0.0), 1.0):.2f}")
    if title:
        out.append(f"title={quote(title)}")
    if artist:
        out.append(f"artist={quote(artist)}")
    return out


def run_model(audio: Path, output_dir: Path, difficulty: float, seed: int, extra: Sequence[str] = (),
              home: Optional[Path] = None, log: Callable[[str], None] = logger.info) -> Path:
    """One chart from the model: the .osu its inference.py wrote into `output_dir`."""
    home = home or HOME
    if not available(home):
        raise ModelUnavailable(tr("没有安装 Mapperatorinator：在面板勾选「实验：Mapperatorinator 模型落键」会自动安装，"
                                  "或运行 python -m proj7k.generator.mapperatorinator --install",
                                  "Mapperatorinator isn't installed: ticking \"Experimental: Mapperatorinator notes\" "
                                  "in the dashboard installs it, or run python -m proj7k.generator.mapperatorinator --install"))
    patch(home)
    output_dir.mkdir(parents=True, exist_ok=True)
    before = set(output_dir.glob("*.osu"))
    cmd = [str(venv_python(home)), "inference.py", "-cn", CONFIG, *extra]
    env = child_env(PYTHONUTF8="1", PYTHONIOENCODING="utf-8")
    proc = subprocess.run(cmd, cwd=repo_dir(home), capture_output=True, text=True, encoding="utf-8",
                          errors="replace", env=env)
    made = sorted(set(output_dir.glob("*.osu")) - before, key=lambda p: p.stat().st_mtime)
    if proc.returncode != 0 or not made:
        tail = (proc.stderr or proc.stdout or "").strip()[-800:]
        raise RuntimeError(tr("Mapperatorinator 没有写出谱面：", "Mapperatorinator wrote no chart: ") + tail)
    return made[-1]


def _default_runner(home: Optional[Path], log: Callable[[str], None]) -> Runner:
    def run(audio: Path, out: Path, difficulty: float, seed: int, extra: Sequence[str]) -> Path:
        return run_model(audio, out, difficulty, seed, extra, home=home, log=log)
    return run


def _stars(bm: Beatmap7K):
    notes = [(o.column, o.time / 1000.0, o.end_time / 1000.0 if o.note_type == NoteType.LN else None)
             for o in bm.hit_objects]
    return evaluate_notes(notes)


def next_difficulty(difficulty: float, stars: float, target: float) -> float:
    """The difficulty to ask for next, assuming the engine's stars scale with it."""
    if stars <= 0:
        step = MAX_STEP
    else:
        step = min(max(target / stars, 1.0 / MAX_STEP), MAX_STEP)
    return min(max(difficulty * step, MIN_DIFFICULTY), MAX_DIFFICULTY)


#: The audio extensions its inference.py accepts; osu!lazer keeps audio under extension-less hash names.
AUDIO_SUFFIXES = (".flac", ".mp3", ".ogg", ".wav", ".m4a")


def model_audio(audio: Path, work: Path, audio_filename: str = "") -> Path:
    """The audio as the model can read it. Its loader (pydub) needs a known extension (osu!lazer keeps audio under
    extension-less hash names) and, for anything but .wav, ffmpeg's ffprobe on PATH: without ffprobe the audio is
    decoded here (mono, 24 kHz, above the model's 16 kHz) and handed over as a .wav."""
    suffix = audio.suffix.lower()
    if suffix not in AUDIO_SUFFIXES:
        suffix = Path(audio_filename).suffix.lower()
    if suffix not in AUDIO_SUFFIXES:
        head = audio.read_bytes()[:12]
        suffix = (".ogg" if head[:4] == b"OggS" else ".flac" if head[:4] == b"fLaC" else
                  ".wav" if head[:4] == b"RIFF" else ".m4a" if head[4:8] == b"ftyp" else ".mp3")
    work.mkdir(parents=True, exist_ok=True)
    if suffix != ".wav" and shutil.which("ffprobe") is None:
        from proj7k.generator.audio import load_audio

        decoded = load_audio(audio)
        copy = work / "audio.wav"
        with wave.open(str(copy), "wb") as out:
            out.setnchannels(1)
            out.setsampwidth(2)
            out.setframerate(decoded.sample_rate)
            out.writeframes((np.clip(decoded.samples, -1.0, 1.0) * 32767).astype("<i2").tobytes())
        return copy
    if audio.suffix.lower() == suffix:
        return audio
    copy = work / ("audio" + suffix)
    shutil.copyfile(audio, copy)
    return copy


def gaps(chart: Beatmap7K, beats: float) -> List[tuple]:
    """(start, end) ms of each stretch of at least `beats` beats between two notes."""
    times = sorted({int(round(o.time)) for o in chart.hit_objects})
    beat = legal._beat_at(chart.timing_points)
    return [(a, b) for a, b in zip(times, times[1:]) if b - a >= beats * beat(a)]


def holes(chart: Beatmap7K) -> float:
    """How much of the chart, first note to last, lies in stretches of `HOLE_BEATS` beats or more without a note."""
    times = [o.time for o in chart.hit_objects]
    if len(times) < 2 or max(times) <= min(times):
        return 0.0
    return sum(b - a for a, b in gaps(chart, HOLE_BEATS)) / (max(times) - min(times))


def refill_spans(chart: Beatmap7K, reference: Optional[Beatmap7K] = None) -> List[tuple]:
    """The stretches to write again (`REFILL_BEATS`), longest first: with a reference, only where it has notes."""
    spans = gaps(chart, REFILL_BEATS)
    if reference is not None and reference.hit_objects:
        ref = sorted(o.time for o in reference.hit_objects)
        beat = legal._beat_at(reference.timing_points)

        def mapped(a, b):
            # The reference keeps writing through most of it: at least a note every two beats on average.
            inside = sum(1 for t in ref if a < t < b)
            return inside >= (b - a) / (2 * beat(a))
        spans = [g for g in spans if mapped(*g)]
    return sorted(spans, key=lambda g: g[0] - g[1])[:REFILL_MAX]


def _reuse(chart: Beatmap7K, audio: Path, log: Callable[[str], None]) -> list:
    """The chart's notes with phrases that sound alike charted alike (ADR-0045); unchanged when the audio
    can't be read."""
    from proj7k.generator import reuse
    try:
        objs, copied, total = reuse.reuse_phrases(chart.hit_objects, chart.timing_points, audio)
    except (AudioDecodeError, ValueError) as e:
        log(tr(f"  没有做乐句复用：{e}", f"  no phrase reuse: {e}"))
        return chart.hit_objects
    if copied:
        log(tr(f"  {copied}/{total} 个乐句照前面听起来一样的乐句写", f"  {copied}/{total} phrases charted as an earlier one that sounds alike"))
    return objs


def _read(osu: Union[str, Path], log: Callable[[str], None]) -> Beatmap7K:
    chart = parse_osu_7k(Path(osu).read_text(encoding="utf-8", errors="replace"))
    if chart.circle_size != 7 or not chart.hit_objects:
        raise RuntimeError(tr(f"Mapperatorinator 写出的不是 7K 谱：{osu}", f"Mapperatorinator didn't write a 7K chart: {osu}"))
    # Its windows overlap, so a note can come back twice or land under a hold an earlier window wrote:
    # the chart is made playable before it is rated (legal.py).
    chart.hit_objects, fixed = legal.sanitize(chart.hit_objects, chart.timing_points)
    if fixed:
        log(tr(f"  修正了 {fixed} 个重叠或不合法的音符", f"  fixed {fixed} overlapping or illegal notes"))
    return chart


def trim(chart: Beatmap7K, target: float, tolerance: float, log: Callable[[str], None]):
    """(chart, profile) brought down toward `target` by the downscaler: within `tolerance` if it can (keeping the
    technique first, then not), else the closest it got below the chart's own stars; None when it removes nothing."""
    from proj7k.downscaler.pipeline import DownscaleOptions, downscale_beatmap

    closest = None
    for keep_technique in (True, False):
        try:
            res = downscale_beatmap(chart, DownscaleOptions(target_dan=None, target_sr=target,
                                                            preserve_technique=keep_technique))
        except (ValueError, RuntimeError) as e:
            log(tr(f"  降难没成功：{e}", f"  Trimming failed: {e}"))
            break
        stars = float(res.downscaled_profile.total_stars)
        if res.notes_removed and (closest is None or abs(stars - target) < abs(closest[2] - target)):
            closest = (res.downscaled_beatmap, res.downscaled_profile, stars, res.notes_removed)
        if abs(stars - target) <= tolerance:
            break
    if closest is None:
        log(tr("  降难没有删掉任何音符", "  Trimming removed no notes"))
        return None
    beatmap, profile, stars, removed = closest
    log(tr(f"  → 删去 {removed} 个音符，降到 {stars:.2f}★", f"  → removed {removed} notes, down to {stars:.2f}★"))
    return beatmap, profile


def generate_from_file(audio_path: Union[str, Path], options, runner: Optional[Runner] = None,
                       work_dir: Optional[Path] = None, home: Optional[Path] = None):
    """A `GenerationResult` from the model's runs, the one whose engine stars come closest to the target."""
    from proj7k.generator.generate import GenerationResult, _beatmap

    audio_path = Path(audio_path)
    log = options.log or logger.info
    real_model = runner is None
    runner = runner or _default_runner(home, log)
    work = Path(work_dir) if work_dir else (home or HOME) / "runs" / time.strftime("%Y%m%d-%H%M%S")
    target = float(options.target_stars)
    difficulty = min(max(target * AIM, MIN_DIFFICULTY), MAX_DIFFICULTY)
    runs = max(1, int(options.model_runs or DEFAULT_RUNS))
    hold_ratio = options.model_hold_ratio
    if hold_ratio is None and (options.ln_ratio or options.whole_inverse):
        hold_ratio = 1.0 if options.whole_inverse else options.ln_ratio
    warnings: List[str] = []
    if options.brief is not None or options.stems or options.auto_design:
        warnings.append(tr("设计稿、分轨与按段技法只用于 proj7k 自己的落键，Mapperatorinator 不读它们",
                           "Briefs, stems and per-section techniques apply to proj7k's own notes; Mapperatorinator ignores them"))

    reference = getattr(options, "reference", None)
    audio = model_audio(audio_path, work, getattr(reference, "audio_filename", "") or "") if real_model else audio_path
    title = options.title or getattr(reference, "title", "") or ""
    artist = options.artist or getattr(reference, "artist", "") or ""
    timing_from = None
    if options.timing and reference is not None:
        # Its parser wants a .osu file of the same song. It also takes the chart's online id as a style (that
        # mapper's, and the chart itself if it was in its training set), so the copy goes without one.
        work.mkdir(parents=True, exist_ok=True)
        anonymous = copy.deepcopy(reference)
        anonymous.extra_sections["Metadata"] = {**anonymous.extra_sections.get("Metadata", {}),
                                                "BeatmapID": "0", "BeatmapSetID": "-1"}
        timing_from = work / "reference.osu"
        timing_from.write_text(dump_osu_7k(anonymous), encoding="utf-8")
    elif options.timing or options.bpm:
        warnings.append(tr("Mapperatorinator 只能借用参考谱面的 timing，这次用它自己从音乐里测的",
                           "Mapperatorinator can only borrow a reference chart's timing; it timed the music itself"))
    # A human chart's own breaks are not holes.
    allowed = (holes(reference) if reference is not None and reference.hit_objects else 0.0) + HOLE_MAX
    best = None
    tried = 0
    for i in range(runs):
        seed = options.seed + i
        extra = overrides(audio, work, difficulty, seed, hold_note_ratio=hold_ratio, title=title, artist=artist,
                          timing_from=timing_from, fast=options.model_fast) + list(options.model_args or ())
        log(tr(f"Mapperatorinator 第 {i + 1}/{runs} 次：难度条件 {difficulty:.2f}★，种子 {seed}……",
               f"Mapperatorinator run {i + 1}/{runs}: difficulty {difficulty:.2f}★, seed {seed}…"))
        chart = _read(runner(audio, work, difficulty, seed, extra), log)
        for start, end in refill_spans(chart, reference):
            log(tr(f"  {start / 1000:.1f}–{end / 1000:.1f} 秒没有音符，重写这一段……",
                   f"  no notes from {start / 1000:.1f} to {end / 1000:.1f} s; writing that stretch again…"))
            # The chart so far is the context; it is made of our notes, so it has no online id to copy.
            current = work / f"refill-{seed}.osu"
            current.write_text(dump_osu_7k(chart), encoding="utf-8")
            again = overrides(audio, work, difficulty, seed, hold_note_ratio=hold_ratio, title=title, artist=artist,
                              timing_from=current) + ["add_to_beatmap=true", f"start_time={start + 1}",
                                                      f"end_time={end - 1}"] + list(options.model_args or ())
            before = len(chart.hit_objects)
            chart = _read(runner(audio, work, difficulty, seed, again), log)
            log(tr(f"  → 补上 {len(chart.hit_objects) - before} 个音符", f"  → {len(chart.hit_objects) - before} notes added"))
        if options.model_reuse:
            chart.hit_objects = _reuse(chart, audio, log)
        empty = holes(chart)
        holey = empty > allowed
        if holey:
            log(tr(f"  谱面有 {empty:.0%} 是空白（连续 {HOLE_BEATS} 拍以上没有音符），换一次再写",
                   f"  {empty:.0%} of the chart is empty ({HOLE_BEATS}+ beats without a note); trying another run"))
        profile = _stars(chart)
        stars = float(profile.total_stars)
        tried += 1
        log(tr(f"  → 引擎星级 {stars:.2f}★（目标 {target:.2f}★），{len(chart.hit_objects)} 个音符",
               f"  → engine {stars:.2f}★ (target {target:.2f}★), {len(chart.hit_objects)} notes"))
        written = stars
        good_enough = options.tolerance
        if target + options.tolerance < stars <= target + TRIM_MAX:
            trimmed = trim(chart, target, options.tolerance, log)
            if trimmed is not None:
                chart, profile = trimmed
                stars = float(profile.total_stars)
                good_enough = max(options.tolerance, TRIM_TOLERANCE)
        if best is None or (holey, abs(stars - target)) < (best[6], abs(best[2] - target)):
            best = (chart, profile, stars, difficulty, seed, good_enough, holey, empty)
        if abs(stars - target) <= good_enough and not holey:
            break
        if written < target and difficulty >= MAX_DIFFICULTY:
            # Asked for all it has and still short: further runs only redraw the same ceiling.
            warnings.append(tr(f"Mapperatorinator 的难度条件已到上限（{MAX_DIFFICULTY:g}★），这首歌写不到 {target:.2f}★",
                               f"Mapperatorinator's difficulty condition is at its ceiling ({MAX_DIFFICULTY:g}★); "
                               f"it can't reach {target:.2f}★ on this song"))
            break
        # Steered by what the model wrote, not by the trim: a trim that undershoots would otherwise push it harder.
        difficulty = next_difficulty(difficulty, written, target)

    chart, profile, stars, difficulty, seed, good_enough, holey, empty = best
    if holey:
        warnings.append(tr(f"{tried} 次生成都有大段空白，这张有 {empty:.0%} 没有音符",
                           f"All {tried} runs have long empty stretches; {empty:.0%} of this one has no notes"))
    if abs(stars - target) > good_enough:
        warnings.append(tr(f"{tried} 次生成里最接近的是 {stars:.2f}★，没到目标 ±{options.tolerance:.2f}★",
                           f"The closest of {tried} runs is {stars:.2f}★, outside the target ±{options.tolerance:.2f}★"))
    timing = [tp for tp in chart.timing_points if tp.uninherited]
    label = options.target_label or f"{target:.2f}"
    if options.version is None:
        options.version = f"Mapperatorinator {label} ({stars:.2f})"
    bm = _beatmap(list(chart.hit_objects), timing, options, stars)
    if not title and chart.title:
        bm.title = chart.title
    if not artist and chart.artist:
        bm.artist = chart.artist
    bm.tags = f"proj7k mapperatorinator-{CONFIG} seed{seed} difficulty{difficulty:.2f}"
    if not (options.reference and options.reference.audio_filename):
        bm.audio_filename = audio_path.name
    return GenerationResult(
        beatmap=bm, profile=profile, target_stars=target, intensity=difficulty, timing_source="reference" if timing_from else "model",
        onset_shift_ms=0.0, tempo=None, iterations=tried, warnings=warnings,
    )


def main(argv: Optional[List[str]] = None) -> int:
    import argparse

    p = argparse.ArgumentParser(prog="python3 -m proj7k.generator.mapperatorinator",
                                description="Mapperatorinator 的安装与状态（ADR-0040）")
    p.add_argument("--install", action="store_true", help="安装（克隆固定版本、建 Python 3.10 环境、装 PyTorch）")
    args = p.parse_args(argv)
    if args.install:
        install(lambda m: print(m, flush=True))
    print(tr(f"Mapperatorinator：{'已安装' if available() else '未安装'}（{HOME}）",
             f"Mapperatorinator: {'installed' if available() else 'not installed'} ({HOME})"))
    return 0


if __name__ == "__main__":
    sys.exit(main())
