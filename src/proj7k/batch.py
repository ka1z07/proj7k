from dataclasses import dataclass, asdict
import json
import os
from pathlib import Path
import sys
import traceback
from typing import List, Optional, Dict, Any, Union, Literal
import argparse

from proj7k.parser import parse_osu_7k
from proj7k.features import extract_beatmap_features, BeatmapFeatures, get_dominant_bpm
from proj7k.monotonicity import evaluate_batch_monotonicity
from proj7k.distillation import distill_benchmark_features

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

    def to_dict(self) -> Dict[str, Any]:
        d: Dict[str, Any] = {
            "summary": self.summary.to_dict(),
            "results": [r.to_dict() for r in self.results],
        }
        if self.monotonicity is not None:
            d["monotonicity"] = self.monotonicity
        if self.distillation is not None:
            d["distillation"] = self.distillation
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
) -> List[BenchmarkItem]:
    """
    Parses a manifest input from:
    1. A list of BenchmarkItem instances or dicts.
    2. A structured nested dict: { "Technique Name": { "1st": { "id": ..., ... } } }
    3. A JSON file path pointing to either of the above formats.
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

    return items


def run_benchmark_pipeline(
    manifest: Union[str, Path, List[Union[BenchmarkItem, Dict[str, Any]]], Dict[str, Any]],
    base_dir: Optional[Union[str, Path]] = None,
    evaluate_monotonicity: bool = True,
    monotonicity_metrics: Optional[List[str]] = None,
    apply_scaling: bool = True,
    distill_features: bool = True,
    ground_truth_output: Optional[str] = None,
) -> BenchmarkBatchReport:
    """
    Executes the top-level benchmark batch pipeline on the provided manifest.
    - Ingests and parses beatmaps via .osu AST.
    - Extracts baseline and physiological features.
    - Evaluates tier sequence monotonicity across techniques.
    - Pre-applies Inverse BPM Scaling Law gating operator for LN Inverse.
    - Distills technique fingerprints and computes orthogonality separability matrix.
    - Fault-tolerant: isolates individual beatmap failures as FAILED_INGESTION.
    - Returns standardized BenchmarkBatchReport.
    """
    items = load_manifest(manifest, base_dir=base_dir)
    results: List[BenchmarkItemResult] = []
    success_count = 0
    failed_count = 0

    for item in items:
        try:
            if item.content is not None:
                bm = parse_osu_7k(item.content)
            elif item.osu_path is not None:
                bm = parse_osu_7k(item.osu_path)
            else:
                raise ValueError("Neither 'content' nor 'osu_path' provided for beatmap")

            effective_bpm = item.bpm
            if effective_bpm is None and bm.timing_points:
                effective_bpm = get_dominant_bpm(bm)

            features = extract_beatmap_features(bm, bpm=effective_bpm)
            results.append(
                BenchmarkItemResult(
                    technique=item.technique,
                    tier=item.tier,
                    id=item.id,
                    song=item.song,
                    bpm=effective_bpm,
                    status="SUCCESS",
                    features=features,
                    error=None,
                )
            )
            success_count += 1
        except Exception as e:
            tb_str = traceback.format_exc()
            results.append(
                BenchmarkItemResult(
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
            )
            failed_count += 1

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

    return BenchmarkBatchReport(
        summary=summary,
        results=results,
        monotonicity=mono_reports,
        distillation=distillation_dict,
    )


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(
        prog="python3 -m proj7k.batch",
        description="Top-level automated benchmark batch pipeline for om7k.",
    )
    parser.add_argument("--manifest", required=True, help="Path to benchmark manifest JSON file")
    parser.add_argument("--base-dir", help="Base directory containing .osu files for path resolution")
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

    args = parser.parse_args(argv if argv is not None else sys.argv[1:])

    try:
        report = run_benchmark_pipeline(
            args.manifest,
            base_dir=args.base_dir,
            apply_scaling=not args.no_scaling,
            ground_truth_output=args.ground_truth_output,
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

    return 0


if __name__ == "__main__":
    sys.exit(main())
