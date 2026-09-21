from dataclasses import dataclass, asdict
import json
import os
from pathlib import Path
import sys
import traceback
import argparse
import concurrent.futures
from typing import List, Optional, Dict, Any, Union, Literal, Tuple

from proj7k.parser import parse_osu_7k
from proj7k.features import extract_beatmap_features, BeatmapFeatures, get_dominant_bpm
from proj7k.monotonicity import evaluate_batch_monotonicity
from proj7k.distillation import distill_benchmark_features
from proj7k.assets import bind_manifest_to_library, scan_local_asset_library
from proj7k.cache import TwoLayerCache
from proj7k.checksum import compute_feature_checksum

IngestionStatus = Literal["SUCCESS", "FAILED_INGESTION"]


@dataclass
class BenchmarkItem:
    technique: str
    tier: str
    id: Optional[int] = None
    song: Optional[str] = None
    osu_path: Optional[str] = None
    content: Optional[str] = None
    bpm: Optional[float] = None
    ref_sr: Optional[float] = None
    sr: Optional[float] = None

    def __post_init__(self):
        if self.ref_sr is None and self.sr is not None:
            self.ref_sr = self.sr
        elif self.sr is None and self.ref_sr is not None:
            self.sr = self.ref_sr


@dataclass
class BatchSummary:
    total: int
    success: int
    failed: int

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass
class BenchmarkItemResult:
    technique: str
    tier: str
    status: IngestionStatus
    id: Optional[int] = None
    song: Optional[str] = None
    bpm: Optional[float] = None
    features: Optional[BeatmapFeatures] = None
    error: Optional[str] = None
    traceback: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        return {
            "technique": self.technique,
            "tier": self.tier,
            "status": self.status,
            "id": self.id,
            "song": self.song,
            "bpm": self.bpm,
            "features": self.features.to_dict() if self.features else None,
            "error": self.error,
            "traceback": self.traceback,
        }


@dataclass
class BenchmarkBatchReport:
    summary: BatchSummary
    results: List[BenchmarkItemResult]
    monotonicity: Optional[Dict[str, Dict[str, Any]]] = None
    distillation: Optional[Dict[str, Any]] = None
    cache_stats: Optional[Dict[str, int]] = None
    feature_checksum: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        d: Dict[str, Any] = {
            "summary": self.summary.to_dict(),
            "results": [r.to_dict() for r in self.results],
        }
        if self.monotonicity is not None:
            d["monotonicity"] = self.monotonicity
        if self.distillation is not None:
            d["distillation"] = self.distillation
        if self.cache_stats is not None:
            d["cache_stats"] = self.cache_stats
        if self.feature_checksum is not None:
            d["feature_checksum"] = self.feature_checksum
        return d

    def to_json(self, indent: int = 2) -> str:
        return json.dumps(self.to_dict(), indent=indent, ensure_ascii=False)

    def save_json(self, path: str) -> None:
        with open(path, "w", encoding="utf-8") as f:
            f.write(self.to_json())


def _resolve_osu_path(
    osu_path: Optional[str],
    beatmap_id: Optional[int],
    base_dir: Optional[Union[str, Path]],
) -> Optional[str]:
    if not osu_path and base_dir and beatmap_id:
        candidate = Path(base_dir) / f"{beatmap_id}.osu"
        if candidate.exists():
            return str(candidate)
    elif osu_path and base_dir and not os.path.isabs(osu_path):
        return str(Path(base_dir) / osu_path)
    return osu_path


def load_manifest(
    manifest_input: Union[str, Path, List[Union[BenchmarkItem, Dict[str, Any]]], Dict[str, Any]],
    base_dir: Optional[Union[str, Path]] = None,
    library_dir: Optional[Union[str, Path]] = None,
) -> List[BenchmarkItem]:
    """
    Parses a manifest input from:
    1. A list of BenchmarkItem instances or dicts.
    2. A structured nested dict: { "Technique Name": { "1st": { "id": ..., ... } } }
    3. A JSON file path pointing to either of the above formats.
    Optionally binds items against a local asset library directory if library_dir is specified.
    """
    raw_data: Any = manifest_input

    if isinstance(manifest_input, (str, Path)):
        p = Path(manifest_input)
        if p.is_file():
            with open(p, "r", encoding="utf-8") as f:
                raw_data = json.load(f)
        else:
            raise FileNotFoundError(f"Manifest file not found: {manifest_input}")

    items: List[BenchmarkItem] = []

    if isinstance(raw_data, list):
        for entry in raw_data:
            if isinstance(entry, BenchmarkItem):
                items.append(entry)
            elif isinstance(entry, dict):
                resolved_path = _resolve_osu_path(entry.get("osu_path"), entry.get("id"), base_dir)
                items.append(
                    BenchmarkItem(
                        technique=entry["technique"],
                        tier=entry["tier"],
                        id=entry.get("id"),
                        song=entry.get("song"),
                        osu_path=resolved_path,
                        content=entry.get("content"),
                        bpm=entry.get("bpm"),
                        ref_sr=entry.get("ref_sr") or entry.get("sr"),
                    )
                )
    elif isinstance(raw_data, dict):
        for technique, tiers in raw_data.items():
            if isinstance(tiers, dict):
                for tier, meta in tiers.items():
                    if isinstance(meta, dict):
                        resolved_path = _resolve_osu_path(meta.get("osu_path"), meta.get("id"), base_dir)
                        items.append(
                            BenchmarkItem(
                                technique=technique,
                                tier=tier,
                                id=meta.get("id"),
                                song=meta.get("song"),
                                osu_path=resolved_path,
                                content=meta.get("content"),
                                bpm=meta.get("bpm"),
                                ref_sr=meta.get("ref_sr") or meta.get("sr"),
                            )
                        )

    if library_dir is not None:
        items = bind_manifest_to_library(items, library_dir)

    return items


def process_benchmark_item(
    item: BenchmarkItem,
    cache: Optional[TwoLayerCache] = None,
) -> BenchmarkItemResult:
    """
    Ingests and processes a single benchmark item:
    - Checks Layer 2 feature cache (bypassing AST parsing & feature extraction on hit)
    - Checks Layer 1 AST cache (bypassing raw file parsing on hit)
    - Extracts baseline and physiological features
    - Fault tolerant: catches exceptions and returns FAILED_INGESTION status.
    """
    try:
        raw_content: Optional[str] = None
        if item.content is not None:
            raw_content = item.content
        elif item.osu_path is not None:
            with open(item.osu_path, "r", encoding="utf-8", errors="replace") as f:
                raw_content = f.read()
        else:
            raise ValueError("Neither 'content' nor 'osu_path' provided for beatmap")

        content_hash = cache.compute_content_hash(raw_content) if cache else None

        features: Optional[BeatmapFeatures] = None
        effective_bpm = item.bpm

        # If effective_bpm is pre-specified, probe Layer 2 feature cache directly
        if cache and content_hash and effective_bpm is not None:
            features = cache.get_features(content_hash, bpm=effective_bpm)

        if features is None:
            # Need AST from Layer 1 cache or parser
            bm = None
            if cache and content_hash:
                bm = cache.get_ast(content_hash)
            if bm is None:
                bm = parse_osu_7k(raw_content)
                if cache and content_hash:
                    cache.put_ast(content_hash, bm)

            if effective_bpm is None:
                if bm.timing_points:
                    effective_bpm = get_dominant_bpm(bm)
                # Check Layer 2 feature cache once effective_bpm is resolved
                if cache and content_hash:
                    features = cache.get_features(content_hash, bpm=effective_bpm)

            if features is None:
                features = extract_beatmap_features(bm, bpm=effective_bpm)
                if cache and content_hash:
                    cache.put_features(content_hash, effective_bpm, features)

        return BenchmarkItemResult(
            technique=item.technique,
            tier=item.tier,
            id=item.id,
            song=item.song,
            bpm=effective_bpm,
            status="SUCCESS",
            features=features,
            error=None,
        )
    except Exception as e:
        tb_str = traceback.format_exc()
        return BenchmarkItemResult(
            technique=item.technique,
            tier=item.tier,
            id=item.id,
            song=item.song,
            bpm=item.bpm,
            status="FAILED_INGESTION",
            features=None,
            error=f"{type(e).__name__}: {str(e)}",
            traceback=tb_str,
        )


def _worker_wrapper(
    args: Tuple[BenchmarkItem, Optional[TwoLayerCache]],
) -> Tuple[BenchmarkItemResult, Optional[Dict[str, int]]]:
    item, cache = args
    if cache is not None:
        stats_before = dict(cache.stats)
        res = process_benchmark_item(item, cache=cache)
        delta_stats = {k: cache.stats[k] - stats_before[k] for k in cache.stats}
        return res, delta_stats
    res = process_benchmark_item(item, cache=None)
    return res, None


def run_benchmark_pipeline(
    manifest: Union[str, Path, List[Union[BenchmarkItem, Dict[str, Any]]], Dict[str, Any]],
    base_dir: Optional[Union[str, Path]] = None,
    library_dir: Optional[Union[str, Path]] = None,
    evaluate_monotonicity: bool = True,
    monotonicity_metrics: Optional[List[str]] = None,
    apply_scaling: bool = True,
    distill_features: bool = True,
    ground_truth_output: Optional[str] = None,
    cache: Optional[TwoLayerCache] = None,
    enable_cache: bool = True,
    cache_dir: Optional[Union[str, Path]] = None,
    workers: int = 1,
    log_progress: bool = False,
) -> BenchmarkBatchReport:
    """
    Executes the top-level benchmark batch pipeline on the provided manifest.
    - Ingests and parses beatmaps via .osu AST.
    - Leverages two-layer persistent cache (AST and Feature Tensor).
    - Supports multi-processing parallel execution with workers.
    - Extracts baseline and physiological features.
    - Evaluates tier sequence monotonicity across techniques.
    - Pre-applies Inverse BPM Scaling Law gating operator for LN Inverse.
    - Distills technique fingerprints and computes orthogonality separability matrix.
    - Fault-tolerant: isolates individual beatmap failures as FAILED_INGESTION.
    - Returns standardized BenchmarkBatchReport.
    """
    items = load_manifest(manifest, base_dir=base_dir, library_dir=library_dir)
    results: List[BenchmarkItemResult] = []

    active_cache = cache
    if active_cache is None and enable_cache:
        active_cache = TwoLayerCache(cache_dir=cache_dir, enabled=True)
    elif not enable_cache and active_cache is not None:
        active_cache.enabled = False

    if workers > 1 and len(items) > 1:
        tasks = [(item, active_cache) for item in items]
        with concurrent.futures.ProcessPoolExecutor(max_workers=workers) as executor:
            worker_outputs = list(executor.map(_worker_wrapper, tasks))

        for idx, (res, w_stats) in enumerate(worker_outputs):
            results.append(res)
            if active_cache and w_stats:
                for k, v in w_stats.items():
                    active_cache.stats[k] += v
            if log_progress:
                item = items[idx]
                print(
                    f"[{idx+1}/{len(items)}] {item.technique} {item.tier} "
                    f"({item.song or item.id}) -> {res.status}",
                    file=sys.stderr,
                )
    else:
        for idx, item in enumerate(items):
            res = process_benchmark_item(item, cache=active_cache)
            results.append(res)
            if log_progress:
                print(
                    f"[{idx+1}/{len(items)}] {item.technique} {item.tier} "
                    f"({item.song or item.id}) -> {res.status}",
                    file=sys.stderr,
                )

    success_count = sum(1 for r in results if r.status == "SUCCESS")
    failed_count = sum(1 for r in results if r.status == "FAILED_INGESTION")

    summary = BatchSummary(
        total=len(items),
        success=success_count,
        failed=failed_count,
    )

    mono_reports = None
    if evaluate_monotonicity:
        mono_reports = evaluate_batch_monotonicity(
            results,
            metrics=monotonicity_metrics,
            apply_scaling=apply_scaling,
        )

    distillation_dict = None
    if distill_features:
        distillation_res = distill_benchmark_features(results)
        distillation_dict = distillation_res.to_dict()
        if ground_truth_output:
            distillation_res.export_ground_truth(ground_truth_output)

    cache_stats_dict = dict(active_cache.stats) if active_cache else None
    feat_checksum = compute_feature_checksum(results)

    return BenchmarkBatchReport(
        summary=summary,
        results=results,
        monotonicity=mono_reports,
        distillation=distillation_dict,
        cache_stats=cache_stats_dict,
        feature_checksum=feat_checksum,
    )


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(
        prog="python3 -m proj7k.batch",
        description="Top-level automated benchmark batch pipeline for om7k.",
    )
    parser.add_argument("--manifest", required=True, help="Path to benchmark manifest JSON file")
    parser.add_argument("--base-dir", help="Base directory containing .osu files for path resolution")
    parser.add_argument("--library-dir", help="Local asset library directory to scan and automatically bind .osu files")
    parser.add_argument("--cache-dir", help="Directory path for persistent two-layer cache")
    parser.add_argument("--no-cache", action="store_true", help="Disable persistent two-layer caching")
    parser.add_argument("-j", "--workers", type=int, default=1, help="Number of worker processes for parallel batch execution")
    parser.add_argument("-v", "--verbose", action="store_true", help="Print real-time batch processing progress log")
    parser.add_argument("-o", "--output", help="Path to output JSON execution report")
    parser.add_argument(
        "--ground-truth-output",
        help="Path to export Ground Truth distillation benchmark dataset JSON",
    )
    parser.add_argument(
        "--no-scaling",
        action="store_true",
        help="Disable Inverse BPM Scaling Law gating operator",
    )
    parser.add_argument(
        "--guard",
        action="store_true",
        help="Execute CI Monotonicity Guard and exit 1 if any monotonicity constraint is violated",
    )
    parser.add_argument(
        "--expected-checksum",
        help="Expected deterministic feature checksum string (sha256:...)",
    )
    parser.add_argument(
        "--guard-metric",
        action="append",
        dest="guard_metrics",
        help="Specific metrics to validate in Monotonicity Guard (default: tier-monotone metrics only)",
    )
    # Guard threshold defaults are read off the guard's own config so the CLI and the
    # library can never disagree about what "the default gate" means.
    from proj7k.guard import MonotonicityGuardConfig

    guard_defaults = MonotonicityGuardConfig()
    parser.add_argument(
        "--guard-max-violations",
        type=int,
        default=guard_defaults.max_violations,
        help=(
            "Maximum allowed monotonicity violations per metric "
            f"(default: {guard_defaults.max_violations})"
        ),
    )
    parser.add_argument(
        "--guard-min-tau",
        type=float,
        default=guard_defaults.min_kendall_tau,
        help=f"Minimum Kendall's tau threshold (default: {guard_defaults.min_kendall_tau})",
    )
    parser.add_argument(
        "--guard-min-rho",
        type=float,
        default=guard_defaults.min_spearman_rho,
        help=f"Minimum Spearman's rho threshold (default: {guard_defaults.min_spearman_rho})",
    )

    args = parser.parse_args(argv if argv is not None else sys.argv[1:])

    try:
        report = run_benchmark_pipeline(
            args.manifest,
            base_dir=args.base_dir,
            library_dir=args.library_dir,
            apply_scaling=not args.no_scaling,
            ground_truth_output=args.ground_truth_output,
            enable_cache=not args.no_cache,
            cache_dir=args.cache_dir,
            workers=args.workers,
            log_progress=args.verbose,
        )
    except Exception as e:
        print(f"Pipeline initialization error: {e}", file=sys.stderr)
        return 1

    if args.output:
        report.save_json(args.output)
        print(
            f"Batch pipeline complete: {report.summary.success}/{report.summary.total} succeeded "
            f"({report.summary.failed} failed). Report saved to {args.output}"
        )
    else:
        print(report.to_json())

    if args.guard:
        from proj7k.guard import evaluate_monotonicity_guard, MonotonicityGuardConfig
        guard_cfg = MonotonicityGuardConfig(
            expected_checksum=args.expected_checksum,
            max_violations=args.guard_max_violations,
            min_kendall_tau=args.guard_min_tau,
            min_spearman_rho=args.guard_min_rho,
        )
        # Only override the default metric set when the caller asked for specific metrics.
        if args.guard_metrics:
            guard_cfg.metrics = list(args.guard_metrics)
        guard_res = evaluate_monotonicity_guard(report, config=guard_cfg)
        if not guard_res.passed:
            print(f"CI Monotonicity Guard Failed:\n{guard_res.error_message}", file=sys.stderr)
            return 1
        else:
            print("CI Monotonicity Guard: PASSED (all techniques and tiers strictly monotonic).", file=sys.stderr)

    return 0


if __name__ == "__main__":
    sys.exit(main())
