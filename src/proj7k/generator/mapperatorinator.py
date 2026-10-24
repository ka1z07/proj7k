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
from proj7k.i18n import tr
from proj7k.parser import Beatmap7K, NoteType, parse_osu_7k

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
DEFAULT_YEAR = 2023

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


def install(log: Callable[[str], None] = logger.info, home: Optional[Path] = None) -> None:
    """Clone the pinned commit and build its Python 3.10 environment (once; a few GB with PyTorch)."""
    home = home or HOME
    if available(home):
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
    _run(["git", "-C", repo, "checkout", "--quiet", COMMIT], log)
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
              year: int = DEFAULT_YEAR, title: str = "", artist: str = "") -> List[str]:
    out = [
        f"audio_path={quote(Path(audio).resolve().as_posix())}",
        f"output_path={quote(Path(output_dir).resolve().as_posix())}",
        "gamemode=3", "keycount=7",
        f"difficulty={difficulty:.2f}", f"seed={int(seed)}", f"year={int(year)}",
        "export_osz=false",
    ]
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
    difficulty = min(max(target, MIN_DIFFICULTY), MAX_DIFFICULTY)
    runs = max(1, int(options.model_runs or DEFAULT_RUNS))
    hold_ratio = options.model_hold_ratio
    if hold_ratio is None and (options.ln_ratio or options.whole_inverse):
        hold_ratio = 1.0 if options.whole_inverse else options.ln_ratio
    warnings: List[str] = []
    if options.timing:
        warnings.append(tr("Mapperatorinator 用它自己从音乐里测的 timing，没有借用参考谱面的 timing",
                           "Mapperatorinator uses the timing it hears in the music, not the reference chart's"))
    if options.brief is not None or options.stems or options.auto_design:
        warnings.append(tr("设计稿、分轨与按段技法只用于 proj7k 自己的落键，Mapperatorinator 不读它们",
                           "Briefs, stems and per-section techniques apply to proj7k's own notes; Mapperatorinator ignores them"))

    reference = getattr(options, "reference", None)
    audio = model_audio(audio_path, work, getattr(reference, "audio_filename", "") or "") if real_model else audio_path
    title = options.title or getattr(reference, "title", "") or ""
    artist = options.artist or getattr(reference, "artist", "") or ""
    best = None
    tried = 0
    for i in range(runs):
        seed = options.seed + i
        extra = overrides(audio, work, difficulty, seed, hold_note_ratio=hold_ratio,
                          title=title, artist=artist) + list(options.model_args or ())
        log(tr(f"Mapperatorinator 第 {i + 1}/{runs} 次：难度条件 {difficulty:.2f}★，种子 {seed}……",
               f"Mapperatorinator run {i + 1}/{runs}: difficulty {difficulty:.2f}★, seed {seed}…"))
        osu = runner(audio, work, difficulty, seed, extra)
        chart = parse_osu_7k(Path(osu).read_text(encoding="utf-8", errors="replace"))
        if chart.circle_size != 7 or not chart.hit_objects:
            raise RuntimeError(tr(f"Mapperatorinator 写出的不是 7K 谱：{osu}", f"Mapperatorinator didn't write a 7K chart: {osu}"))
        # Its windows overlap, so a note can come back twice or land under a hold an earlier window wrote:
        # the chart is made playable before it is rated (legal.py).
        chart.hit_objects, fixed = legal.sanitize(chart.hit_objects, chart.timing_points)
        if fixed:
            log(tr(f"  修正了 {fixed} 个重叠或不合法的音符", f"  fixed {fixed} overlapping or illegal notes"))
        profile = _stars(chart)
        stars = float(profile.total_stars)
        tried += 1
        log(tr(f"  → 引擎星级 {stars:.2f}★（目标 {target:.2f}★），{len(chart.hit_objects)} 个音符",
               f"  → engine {stars:.2f}★ (target {target:.2f}★), {len(chart.hit_objects)} notes"))
        if best is None or abs(stars - target) < abs(best[2] - target):
            best = (chart, profile, stars, difficulty, seed)
        if abs(stars - target) <= options.tolerance:
            break
        difficulty = next_difficulty(difficulty, stars, target)

    chart, profile, stars, difficulty, seed = best
    if abs(stars - target) > options.tolerance:
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
        beatmap=bm, profile=profile, target_stars=target, intensity=difficulty, timing_source="model",
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
