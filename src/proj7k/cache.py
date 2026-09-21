import hashlib
import json
import os
from pathlib import Path
import pickle
import tempfile
from dataclasses import fields
from typing import Optional, Dict, Any, Union

from proj7k.parser import Beatmap7K
from proj7k.features import BeatmapFeatures

ALGORITHM_VERSION: str = "1.0.0"


DEFAULT_CACHE_DIR: Path = Path(".cache/proj7k")


def features_from_dict(payload: Dict[str, Any]) -> BeatmapFeatures:
    """
    Rebuilds BeatmapFeatures from its serialized form (`BeatmapFeatures.to_dict`).

    Driven by the dataclass' own field list rather than a hand-written one: a hand-written list
    quietly drops every field added after it was written, and a dropped field reads back as its
    default — which silently changes any star rating computed from cached features.
    """
    known = {f.name for f in fields(BeatmapFeatures)}
    restored = {name: value for name, value in payload.items() if name in known}

    # JSON stringifies the keys of lockout_profile; the dataclass keys them by finger index.
    if "lockout_profile" in restored:
        restored["lockout_profile"] = {
            int(finger): value for finger, value in restored["lockout_profile"].items()
        }

    return BeatmapFeatures(**restored)


class TwoLayerCache:
    """
    Two-layer persistent cache:
    - Layer 1: AST / HitObject parsing cache (keyed by SHA-256 of .osu file content).
    - Layer 2: Feature tensor cache (keyed by SHA-256 of content + algorithm version + bpm).
    Includes memory residency control (bounded in-memory LRU eviction).
    """

    def __init__(
        self,
        cache_dir: Optional[Union[str, Path]] = DEFAULT_CACHE_DIR,
        enabled: bool = True,
        algorithm_version: str = ALGORITHM_VERSION,
        max_mem_entries: int = 500,
    ):
        self.enabled = enabled
        self.algorithm_version = algorithm_version
        self.cache_dir: Optional[Path] = Path(cache_dir) if (cache_dir is not None and enabled) else None
        self.max_mem_entries: int = max_mem_entries

        self._ast_mem_cache: Dict[str, Beatmap7K] = {}
        self._feat_mem_cache: Dict[str, BeatmapFeatures] = {}

        self.stats: Dict[str, int] = {
            "ast_hits": 0,
            "ast_misses": 0,
            "feature_hits": 0,
            "feature_misses": 0,
        }

        if self.cache_dir and self.enabled:
            self.ast_dir = self.cache_dir / "ast"
            self.features_dir = self.cache_dir / "features"
            self.ast_dir.mkdir(parents=True, exist_ok=True)
            self.features_dir.mkdir(parents=True, exist_ok=True)
        else:
            self.ast_dir = None
            self.features_dir = None

    @staticmethod
    def compute_content_hash(content: Union[str, bytes]) -> str:
        data = content.encode("utf-8") if isinstance(content, str) else content
        return hashlib.sha256(data).hexdigest()

    def _feature_key(self, content_hash: str, bpm: Optional[float]) -> str:
        bpm_tag = f"{round(bpm, 2)}" if bpm is not None else "auto"
        key_str = f"{content_hash}:{self.algorithm_version}:{bpm_tag}"
        return hashlib.sha256(key_str.encode("utf-8")).hexdigest()

    def get_ast(self, content_hash: str) -> Optional[Beatmap7K]:
        if not self.enabled:
            return None

        if content_hash in self._ast_mem_cache:
            self.stats["ast_hits"] += 1
            return self._ast_mem_cache[content_hash]

        if self.ast_dir:
            file_path = self.ast_dir / f"{content_hash}.pkl"
            if file_path.exists():
                try:
                    with open(file_path, "rb") as f:
                        bm = pickle.load(f)
                    self._ast_mem_cache[content_hash] = bm
                    self.stats["ast_hits"] += 1
                    return bm
                except Exception:
                    pass

        self.stats["ast_misses"] += 1
        return None

    def put_ast(self, content_hash: str, beatmap: Beatmap7K) -> None:
        if not self.enabled:
            return

        if len(self._ast_mem_cache) >= self.max_mem_entries:
            self._ast_mem_cache.pop(next(iter(self._ast_mem_cache)), None)
        self._ast_mem_cache[content_hash] = beatmap

        if self.ast_dir:
            file_path = self.ast_dir / f"{content_hash}.pkl"
            try:
                temp_fd, temp_path = tempfile.mkstemp(dir=self.ast_dir, prefix="tmp_ast_")
                with os.fdopen(temp_fd, "wb") as f:
                    pickle.dump(beatmap, f, protocol=pickle.HIGHEST_PROTOCOL)
                os.replace(temp_path, file_path)
            except Exception:
                pass

    def get_features(
        self, content_hash: str, bpm: Optional[float] = None
    ) -> Optional[BeatmapFeatures]:
        if not self.enabled:
            return None

        key = self._feature_key(content_hash, bpm)
        if key in self._feat_mem_cache:
            self.stats["feature_hits"] += 1
            return self._feat_mem_cache[key]

        if self.features_dir:
            file_path = self.features_dir / f"{key}.json"
            if file_path.exists():
                try:
                    with open(file_path, "r", encoding="utf-8") as f:
                        d = json.load(f)
                    feat = features_from_dict(d)
                    self._feat_mem_cache[key] = feat
                    self.stats["feature_hits"] += 1
                    return feat
                except Exception:
                    pass

        self.stats["feature_misses"] += 1
        return None

    def put_features(
        self,
        content_hash: str,
        bpm: Optional[float],
        features: BeatmapFeatures,
    ) -> None:
        if not self.enabled:
            return

        key = self._feature_key(content_hash, bpm)
        if len(self._feat_mem_cache) >= self.max_mem_entries:
            self._feat_mem_cache.pop(next(iter(self._feat_mem_cache)), None)
        self._feat_mem_cache[key] = features

        if self.features_dir:
            file_path = self.features_dir / f"{key}.json"
            try:
                temp_fd, temp_path = tempfile.mkstemp(dir=self.features_dir, prefix="tmp_feat_")
                with os.fdopen(temp_fd, "w", encoding="utf-8") as f:
                    json.dump(features.to_dict(), f, ensure_ascii=False)
                os.replace(temp_path, file_path)
            except Exception:
                pass
