"""
Node.js Realm Bridge Client for osu!lazer database ingestion.
"""

from dataclasses import dataclass, field
import json
import os
from pathlib import Path
import shutil
import subprocess
from typing import Any, Dict, List, Optional, Sequence


DEFAULT_REALM_PATH = Path.home() / "Library" / "Application Support" / "osu" / "client.realm"
DEFAULT_BRIDGE_DIR = Path(__file__).resolve().parents[3] / "tools" / "lazer-bridge"


@dataclass(frozen=True)
class LazerBeatmapRecord:
    id: str
    hash: str
    md5_hash: str
    file_hash: str
    star_rating: float
    difficulty_name: str
    tags: str
    title: str
    artist: str
    ruleset_id: int
    circle_size: float

    @property
    def is_7k_mania(self) -> bool:
        return self.ruleset_id == 3 and abs(self.circle_size - 7.0) < 0.01

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "LazerBeatmapRecord":
        return cls(
            id=str(data.get("id", "")),
            hash=str(data.get("hash", "")),
            md5_hash=str(data.get("md5_hash", "")),
            file_hash=str(data.get("file_hash", data.get("hash", ""))),
            star_rating=float(data.get("star_rating", -1.0)),
            difficulty_name=str(data.get("difficulty_name", "")),
            tags=str(data.get("tags", "")),
            title=str(data.get("title", "")),
            artist=str(data.get("artist", "")),
            ruleset_id=int(data.get("ruleset_id", 0)),
            circle_size=float(data.get("circle_size", 0.0)),
        )


@dataclass(frozen=True)
class BeatmapMutationPayload:
    """Unified payload for updating or reverting beatmap attributes."""
    id: str
    star_rating: float
    difficulty_name: str
    tags: str

    def to_dict(self) -> Dict[str, Any]:
        return {
            "id": self.id,
            "star_rating": self.star_rating,
            "difficulty_name": self.difficulty_name,
            "tags": self.tags,
        }


# Type aliases maintaining backward compatibility and semantic clarity
BeatmapUpdatePayload = BeatmapMutationPayload
BeatmapRevertPayload = BeatmapMutationPayload


@dataclass(frozen=True)
class BatchUpdateResult:
    success: bool
    updated_count: int = 0
    error: Optional[str] = None


@dataclass(frozen=True)
class BridgeEnvironmentStatus:
    has_node: bool
    has_npm: bool
    has_realm_dependency: bool
    bridge_script_exists: bool
    node_path: Optional[str] = None
    npm_path: Optional[str] = None

    @property
    def is_ready(self) -> bool:
        return (
            self.has_node
            and self.has_npm
            and self.has_realm_dependency
            and self.bridge_script_exists
        )


class RealmBridgeError(RuntimeError):
    """Exception raised when the Realm bridge driver fails."""
    pass


class RealmBridgeClient:
    """
    Subprocess client connecting Python proj7k engine to the Node.js Realm driver.
    """

    def __init__(
        self,
        bridge_dir: Optional[Path] = None,
        default_realm_path: Optional[Path] = None,
        node_executable: str = "node",
    ):
        self.bridge_dir = bridge_dir or DEFAULT_BRIDGE_DIR
        self.default_realm_path = default_realm_path or DEFAULT_REALM_PATH
        self.node_executable = node_executable
        self.bridge_script = self.bridge_dir / "bridge.js"

    def check_environment(self) -> BridgeEnvironmentStatus:
        node_path = shutil.which(self.node_executable)
        npm_path = shutil.which("npm")
        realm_dep = (self.bridge_dir / "node_modules" / "realm").exists()
        script_exists = self.bridge_script.exists()

        return BridgeEnvironmentStatus(
            has_node=bool(node_path),
            has_npm=bool(npm_path),
            has_realm_dependency=realm_dep,
            bridge_script_exists=script_exists,
            node_path=node_path,
            npm_path=npm_path,
        )

    def ensure_installed(self) -> bool:
        status = self.check_environment()
        if status.is_ready:
            return True

        if not status.has_node:
            raise RealmBridgeError("Node.js is not installed or not in PATH.")
        if not status.has_npm or not status.npm_path:
            raise RealmBridgeError("npm is not installed or not in PATH.")

        if not self.bridge_dir.exists():
            raise RealmBridgeError(f"Bridge directory not found at {self.bridge_dir}")

        proc = subprocess.run(
            [status.npm_path, "install"],
            cwd=str(self.bridge_dir),
            capture_output=True,
            text=True,
        )
        if proc.returncode != 0:
            raise RealmBridgeError(f"Failed to install Realm dependencies: {proc.stderr}")

        return self.check_environment().is_ready

    def dump_7k_beatmaps(
        self,
        realm_path: Optional[Path] = None,
        auto_setup: bool = True,
    ) -> List[LazerBeatmapRecord]:
        if auto_setup:
            self.ensure_installed()

        target_realm = realm_path or self.default_realm_path
        cmd = [
            self.node_executable,
            str(self.bridge_script),
            "dump-7k",
            "--realm",
            str(target_realm),
        ]
        res = self._run_command(cmd)
        if not res.get("success", False):
            raise RealmBridgeError(res.get("error", "Failed to dump beatmaps"))

        raw_list = res.get("beatmaps", [])
        return [LazerBeatmapRecord.from_dict(item) for item in raw_list]

    def dump_collections(
        self,
        realm_path: Optional[Path] = None,
        auto_setup: bool = True,
    ) -> Dict[str, List[str]]:
        if auto_setup:
            self.ensure_installed()

        target_realm = realm_path or self.default_realm_path
        cmd = [
            self.node_executable,
            str(self.bridge_script),
            "dump-collections",
            "--realm",
            str(target_realm),
        ]
        res = self._run_command(cmd)
        if not res.get("success", False):
            raise RealmBridgeError(res.get("error", "Failed to dump collections"))

        return res.get("collections", {})

    def apply_batch_update(
        self,
        updates: Sequence[BeatmapMutationPayload],
        collections: Optional[Dict[str, List[str]]] = None,
        realm_path: Optional[Path] = None,
        auto_setup: bool = True,
    ) -> BatchUpdateResult:
        if auto_setup:
            self.ensure_installed()

        target_realm = realm_path or self.default_realm_path
        cmd = [
            self.node_executable,
            str(self.bridge_script),
            "update-batch",
            "--realm",
            str(target_realm),
        ]
        payload = {
            "updates": [u.to_dict() for u in updates],
            "collections": collections or {},
        }
        res = self._run_command(cmd, input_data=json.dumps(payload))
        return BatchUpdateResult(
            success=bool(res.get("success", False)),
            updated_count=int(res.get("updated_count", 0)),
            error=res.get("error"),
        )

    def revert_batch(
        self,
        reverts: Sequence[BeatmapMutationPayload],
        collections_to_clean: Optional[List[str]] = None,
        realm_path: Optional[Path] = None,
        auto_setup: bool = True,
    ) -> BatchUpdateResult:
        if auto_setup:
            self.ensure_installed()

        target_realm = realm_path or self.default_realm_path
        cmd = [
            self.node_executable,
            str(self.bridge_script),
            "revert-batch",
            "--realm",
            str(target_realm),
        ]
        payload = {
            "reverts": [r.to_dict() for r in reverts],
            "collections_to_clean": collections_to_clean or [],
        }
        res = self._run_command(cmd, input_data=json.dumps(payload))
        return BatchUpdateResult(
            success=bool(res.get("success", False)),
            updated_count=int(res.get("updated_count", 0)),
            error=res.get("error"),
        )

    def locate_beatmap(
        self,
        online_id: Optional[int] = None,
        file_hash: Optional[str] = None,
        set_id: Optional[int] = None,
        realm_path: Optional[Path] = None,
        auto_setup: bool = True,
    ) -> Optional[Dict[str, Any]]:
        """
        Locates a beatmap in osu!lazer Realm database by online ID, MD5/SHA256 hash, or set ID.
        """
        if auto_setup:
            self.ensure_installed()

        target_realm = realm_path or self.default_realm_path
        cmd = [
            self.node_executable,
            str(self.bridge_script),
            "locate-beatmap",
            "--realm",
            str(target_realm),
        ]
        if online_id is not None:
            cmd.extend(["--online-id", str(online_id)])
        if file_hash is not None:
            cmd.extend(["--hash", str(file_hash)])
        if set_id is not None:
            cmd.extend(["--set-id", str(set_id)])

        res = self._run_command(cmd)
        if not res.get("success", False):
            raise RealmBridgeError(res.get("error", "Failed to locate beatmap"))

        if res.get("found", False):
            return res.get("beatmap")
        return None

    def _run_command(self, cmd: List[str], input_data: Optional[str] = None) -> Dict[str, Any]:
        try:
            proc = subprocess.run(
                cmd,
                input=input_data,
                capture_output=True,
                text=True,
                check=False,
            )
        except FileNotFoundError as e:
            raise RealmBridgeError(f"Command not found: {cmd[0]}") from e

        if proc.returncode != 0:
            err_msg = proc.stderr.strip() or f"Process failed with exit code {proc.returncode}"
            try:
                # Some errors return json on stdout
                parsed = json.loads(proc.stdout)
                if isinstance(parsed, dict) and "error" in parsed:
                    return parsed
            except Exception:
                pass
            return {"success": False, "error": err_msg}

        try:
            return json.loads(proc.stdout)
        except json.JSONDecodeError as e:
            return {
                "success": False,
                "error": f"Failed to parse bridge output: {e}. Output: {proc.stdout[:200]}",
            }
