"""
The dashboard's actions: each runs one feature of the toolchain the way its CLI does, on a job's thread.

An action takes the job's parameters (as the page sent them) and its `JobContext`, logs as it goes, registers the
files it produced, and returns a JSON-able result for the page. A failure is an exception; its message is what the
page shows. Every action here calls the same functions as `proj7k.sync`, `proj7k.downscaler` and
`proj7k.profiler`, so the dashboard and the command line cannot drift apart.
"""

from dataclasses import asdict, dataclass
import logging
from pathlib import Path
import re
import shutil
from typing import Any, Dict, List, Optional

from proj7k.lazer.backup import DEFAULT_CACHE_DIR
from proj7k.lazer.bridge import DEFAULT_REALM_PATH, RealmBridgeClient

from proj7k.dashboard.jobs import Action, JobContext

logger = logging.getLogger("proj7k.dashboard")

#: Where the dashboard keeps its files unless told otherwise: job scratch space, and the outputs people keep.
DEFAULT_DASHBOARD_DIR = DEFAULT_CACHE_DIR / "dashboard"


@dataclass(frozen=True)
class DashboardConfig:
    """The osu!lazer library, the profiler database and the output folders every action works against."""
    realm_path: Path = DEFAULT_REALM_PATH
    files_dir: Optional[Path] = None
    cache_dir: Path = DEFAULT_CACHE_DIR
    lock_path: Optional[Path] = None
    db_path: Optional[Path] = None
    output_dir: Path = DEFAULT_DASHBOARD_DIR

    @property
    def lazer_files_dir(self) -> Path:
        return self.files_dir or (self.realm_path.parent / "files")

    @property
    def lazer_lock_path(self) -> Path:
        return self.lock_path or (self.realm_path.parent / "client.realm.lock")

    @property
    def jobs_dir(self) -> Path:
        return self.output_dir / "jobs"

    @property
    def practice_dir(self) -> Path:
        return self.output_dir / "practice_maps"

    @property
    def bundles_dir(self) -> Path:
        return self.output_dir / "practice_bundles"

    @property
    def views_dir(self) -> Path:
        return self.output_dir / "replay_views"


# --- parameter helpers -----------------------------------------------------------------------------------------


def _text(params: Dict[str, Any], key: str) -> str:
    value = params.get(key)
    return str(value).strip() if value is not None else ""


def _path(params: Dict[str, Any], key: str, what: str) -> Path:
    raw = _text(params, key).strip('"')
    if not raw:
        raise ValueError(f"缺少{what}")
    path = Path(raw).expanduser()
    if not path.exists():
        raise FileNotFoundError(f"{what}不存在：{path}")
    return path


def _float(params: Dict[str, Any], key: str) -> Optional[float]:
    value = params.get(key)
    if value is None or (isinstance(value, str) and not value.strip()):
        return None
    return float(value)


def _int(params: Dict[str, Any], key: str) -> Optional[int]:
    value = _float(params, key)
    return int(value) if value is not None else None


def _flag(params: Dict[str, Any], key: str, default: bool = False) -> bool:
    value = params.get(key, default)
    if isinstance(value, str):
        return value.strip().lower() in ("1", "true", "yes", "on")
    return bool(value)


def _safe_label(text: str) -> str:
    return re.sub(r"[^\w.-]+", "_", text).strip("_")[:120] or "replay"


# --- sync ------------------------------------------------------------------------------------------------------


def _sync_manager(config: DashboardConfig):
    from proj7k.lazer.daemon import LazerSyncManager, SyncOptions

    return LazerSyncManager(options=SyncOptions(
        realm_path=config.realm_path,
        files_dir=config.lazer_files_dir,
        cache_dir=config.cache_dir,
        lock_path=config.lock_path,
    ))


def sync_setup(config: DashboardConfig, params: Dict[str, Any], ctx: JobContext) -> Dict[str, Any]:
    logger.info("Installing / verifying the Node.js Realm bridge...")
    RealmBridgeClient(default_realm_path=config.realm_path).ensure_installed()
    logger.info("Bridge ready.")
    return {"ready": True}


def sync_once(config: DashboardConfig, params: Dict[str, Any], ctx: JobContext) -> Dict[str, Any]:
    dry_run = _flag(params, "dry_run")
    logger.info(f"{'Dry run over' if dry_run else 'Synchronizing'} osu!lazer database at '{config.realm_path}'...")
    summary = _sync_manager(config).sync_once(wait_for_lock=_flag(params, "wait"), dry_run=dry_run)
    if not summary.success:
        raise RuntimeError(summary.error or "同步失败")
    return asdict(summary)


def sync_revert(config: DashboardConfig, params: Dict[str, Any], ctx: JobContext) -> Dict[str, Any]:
    logger.info(f"Reverting all proj7k modifications on '{config.realm_path}'...")
    res = _sync_manager(config).revert_all()
    if not res.success:
        raise RuntimeError(res.error or "还原失败")
    return {"restored": res.updated_count}


# --- downscaler ------------------------------------------------------------------------------------------------


def _tech_key(skill: Optional[str]) -> Optional[str]:
    """The short technique key (`speed`) of an engine skill name (`rc_speed`), as every page labels skills."""
    from proj7k.engine.skills import SKILL_TECH_KEY

    return SKILL_TECH_KEY.get(skill, skill) if skill else skill


def _profile_skills(profile) -> Dict[str, float]:
    from proj7k.engine.skills import SKILL_TECH_KEY

    return {SKILL_TECH_KEY[skill]: round(value.stars, 3) for skill, value in profile.skills.items()}


def downscale(config: DashboardConfig, params: Dict[str, Any], ctx: JobContext) -> Dict[str, Any]:
    from proj7k.downscaler.cli import run_downscale
    from proj7k.downscaler.pipeline import DownscaleOptions

    source = _text(params, "input").strip('"')
    if not source:
        raise ValueError("缺少输入谱面（.osu 路径、文件夹、谱面 ID 或链接）")
    target_dan = _text(params, "target_dan") or None
    target_sr = _float(params, "target_sr")
    if target_dan is None and target_sr is None:
        raise ValueError("请选择目标段位或填写目标星级")
    options = DownscaleOptions(
        target_dan=target_dan,
        target_sr=target_sr,
        dominant_skill=_text(params, "dominant_skill") or None,
    )
    output_dir = Path(_text(params, "output_dir").strip('"')).expanduser() if _text(params, "output_dir") else config.practice_dir
    run = run_downscale(
        source,
        options,
        output_dir=output_dir,
        package=_flag(params, "package", True),
        sync_lazer=_flag(params, "sync_lazer"),
        realm_path=config.realm_path,
        lock_path=config.lock_path,
    )
    if run.error:
        raise RuntimeError(run.error)

    charts: List[Dict[str, Any]] = []
    for res, out_path, src_path in run.results:
        bm = res.original_beatmap
        osz = run.osz_map.get(out_path) if out_path else None
        if osz is not None:
            ctx.add_artifact(osz, "练习包 .osz")
        if out_path is not None:
            ctx.add_artifact(out_path, "练习谱 .osu")
        report = res.to_dict()
        validation = report["validation"]
        for key in ("dominant_technique_orig", "dominant_technique_downscaled"):
            validation[key] = _tech_key(validation[key])
        charts.append({
            "title": f"{bm.artist} - {bm.title} [{bm.version}]",
            "version": res.downscaled_beatmap.version,
            "source": str(src_path),
            "original_stars": report["original_star_rating"],
            "downscaled_stars": report["downscaled_star_rating"],
            "original_notes": len(bm.hit_objects),
            "downscaled_notes": len(res.downscaled_beatmap.hit_objects),
            "removal_ratio": report["removal_ratio"],
            "bimanual_flux_ratio": report["bimanual_flux_ratio"],
            "target": report["target"],
            "validation": validation,
            "warnings": report["warnings"],
            "skills_original": _profile_skills(res.original_profile),
            "skills_downscaled": _profile_skills(res.downscaled_profile),
            "output_file": str(out_path) if out_path else None,
            "osz_file": str(osz) if osz else None,
        })
    sync = run.to_dict()["sync"]
    return {"charts": charts, "failed_count": run.failed_count, "sync": sync, "output_dir": str(output_dir)}


# --- profiler --------------------------------------------------------------------------------------------------


def _report_summary(report) -> Dict[str, Any]:
    return {
        "player_name": report.player_name,
        "hash_matched": report.hash_matched,
        "mods": report.mods,
        "clock_rate": report.clock_rate,
        "official_counts": report.official_counts,
        "judgment_counts": {k.value: v for k, v in report.judgment_counts.items()},
        "total_notes": report.total_notes,
        "total_hits": report.total_hits,
        "miss_count": report.miss_count,
        "ghost_tap_count": report.ghost_tap_count,
        "play_duration_s": round(report.play_duration_s, 2),
        "completion_rate": round(report.completion_rate, 4),
        "is_valid_play": report.is_valid_play,
        "pathology": report.pathology.to_dict() if report.pathology else None,
        "skill_radar": report.skill_radar.to_dict() if report.skill_radar else None,
    }


def _beatmap_title(report) -> str:
    bm = report.beatmap
    if bm is None:
        return ""
    return f"{bm.artist} - {bm.title} [{bm.version}]"


def profile_replay(config: DashboardConfig, params: Dict[str, Any], ctx: JobContext) -> Dict[str, Any]:
    """Diagnose one replay: from two files, or the player's N-th most recent 7K replay in the lazer library."""
    from proj7k.parser import parse_osu_7k
    from proj7k.profiler.cli import build_practice_bundle, resolve_lazer_replay, run_ingestion
    from proj7k.profiler.coach import generate_coaching_recommendations
    from proj7k.profiler.replay_view import find_audio, write_replay_view
    from proj7k.profiler.storage import ProfilerStorage

    audio: Optional[Path] = None
    if _text(params, "source") == "lazer":
        player = _text(params, "player")
        if not player:
            raise ValueError("请填写玩家名")
        found = resolve_lazer_replay(
            player, realm_path=config.realm_path, files_dir=config.lazer_files_dir, index=_int(params, "index") or 0,
        )
        replay_path, beatmap_path = found["replay"], found["beatmap"]
        label = _safe_label(f"{found['title']}_{found['difficulty_name']}_{found['date'][:10]}")
        logger.info(f"Latest lazer replay: {found['title']} [{found['difficulty_name']}] {found['date'][:19]}")
    else:
        replay_path = _path(params, "replay", "回放文件")
        beatmap_path = _path(params, "beatmap", "谱面文件")
        label = _safe_label(Path(replay_path).stem)

    logger.info("Aligning the replay against the chart and running the diagnosis...")
    report = run_ingestion(replay_path, beatmap_path)
    if _text(params, "source") != "lazer":
        audio = find_audio(Path(beatmap_path), report.beatmap.audio_filename if report.beatmap else "")

    saved = None
    if _flag(params, "save", True):
        with ProfilerStorage(db_path=config.db_path) as storage:
            saved = storage.save_report_with_filter(report, beatmap=parse_osu_7k(str(beatmap_path)))
        logger.info("Saved to the profiler database." if saved is not None else "Not saved (noise-filtered or already in the database).")

    result: Dict[str, Any] = {
        "title": _beatmap_title(report),
        "report": _report_summary(report),
        "saved": saved is not None,
    }

    if _flag(params, "view", True):
        view_dir = config.views_dir
        view_dir.mkdir(parents=True, exist_ok=True)
        view_audio = None
        if audio is not None and audio.is_file():
            # The page references its audio relatively; a copy beside it keeps that link valid when served.
            view_audio = view_dir / f"{label}{audio.suffix.lower()}"
            if not view_audio.exists():
                shutil.copyfile(audio, view_audio)
        view = write_replay_view(
            report, view_dir / f"{label}.html", audio_path=view_audio, replay_path=replay_path, beatmap_path=beatmap_path,
        )
        ctx.add_artifact(view, "回放查看器")
        if view_audio is not None:
            ctx.add_artifact(view_audio, "音频")
        result["replay_view"] = str(view)

    strategy = _text(params, "recommend")
    if strategy:
        recs = generate_coaching_recommendations(
            report, strategy=strategy, realm_path=config.realm_path if config.realm_path.exists() else None,
        )
        result["recommendations"] = [r.to_dict() for r in recs]

    if _flag(params, "bundle"):
        bundle = build_practice_bundle(
            report, beatmap_path, fatal_time_ms=_float(params, "fatal_time"), bundle_dir=config.bundles_dir / label,
        )
        if bundle is None:
            result["bundle_notice"] = "回放里没有找到崩盘点；填写「切片时间」可以指定一段来生成练习包。"
        else:
            if bundle.combined_osz_path is not None:
                ctx.add_artifact(bundle.combined_osz_path, "三阶练习包 .osz")
            for name, tier in bundle.tiers.items():
                if tier.osz_path is not None:
                    ctx.add_artifact(tier.osz_path, f"{name} 阶 .osz")
            result["bundle"] = bundle.to_dict()
    return result


def profile_player(config: DashboardConfig, params: Dict[str, Any], ctx: JobContext) -> Dict[str, Any]:
    from proj7k.profiler.aggregate import aggregate_macro_profile
    from proj7k.profiler.coach import generate_coaching_recommendations
    from proj7k.profiler.storage import ProfilerStorage

    player = _text(params, "player")
    if not player:
        raise ValueError("请填写玩家名")
    all_time = _flag(params, "all_time")
    horizon = None if all_time else (_float(params, "horizon_days") or 30.0)
    with ProfilerStorage(db_path=config.db_path) as storage:
        profile = aggregate_macro_profile(
            storage=storage, player_name=player, horizon_days=horizon, compare_against_all_time=not all_time,
        )
    result: Dict[str, Any] = {"profile": profile.to_dict()}
    strategy = _text(params, "recommend")
    if strategy:
        recs = generate_coaching_recommendations(
            profile, strategy=strategy, realm_path=config.realm_path if config.realm_path.exists() else None,
        )
        result["recommendations"] = [r.to_dict() for r in recs]
    return result


def import_replays(config: DashboardConfig, params: Dict[str, Any], ctx: JobContext) -> Dict[str, Any]:
    from proj7k.profiler.cli import run_replay_import

    player = _text(params, "player")
    if not player:
        raise ValueError("请填写玩家名")
    logger.info(f"Importing {player}'s 7K replays from the osu!lazer library...")
    stats = run_replay_import(
        player_name=player,
        realm_path=config.realm_path,
        files_dir=config.lazer_files_dir,
        db_path=config.db_path,
        limit=_int(params, "limit"),
    )
    logger.info(f"Imported {stats['saved']} new replay(s); {stats['already_exists']} already in the database.")
    return stats


def build_actions(config: DashboardConfig) -> Dict[str, Action]:
    """The job kinds the dashboard accepts, bound to `config`."""
    def bind(fn):
        return lambda params, ctx: fn(config, params, ctx)

    return {
        "sync_setup": bind(sync_setup),
        "sync_once": bind(sync_once),
        "sync_revert": bind(sync_revert),
        "downscale": bind(downscale),
        "profile_replay": bind(profile_replay),
        "profile_player": bind(profile_player),
        "import_replays": bind(import_replays),
    }
