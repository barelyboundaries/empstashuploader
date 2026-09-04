"""
Run Store for the Empornium Megapack Builder.
Provides bounded in-memory and persistent on-disk storage for task run results,
allowing the review UI and standalone history view to inspect past runs.
"""

from __future__ import annotations

import json
import logging
import os
from pathlib import Path
import re
import threading
import time
from typing import Any

logger = logging.getLogger(__name__)

RUN_ID_REGEX = re.compile(r"^[A-Za-z0-9_-]{1,64}$")


class RunStore:
    def __init__(
        self,
        runs_dir: Path | str | None = None,
        default_ttl: float = 3600.0,
        max_entries: int = 1000,
        max_disk_runs: int = 200,
        auto_load: bool = False,
    ):
        self._runs: dict[str, dict[str, Any]] = {}
        self.default_ttl = default_ttl
        self.max_entries = max_entries
        self.max_disk_runs = max_disk_runs
        self._lock = threading.Lock()

        if runs_dir is not None:
            self.runs_dir: Path | None = Path(runs_dir)
        else:
            try:
                from .config import get_settings
                self.runs_dir = Path(get_settings().runs_dir)
            except Exception:
                from .config import _runtime_default
                self.runs_dir = _runtime_default("runs")

        if auto_load:
            self.load_from_disk()

    def _validate_run_id(self, run_id: str) -> None:
        if not isinstance(run_id, str) or not RUN_ID_REGEX.match(run_id):
            raise ValueError(
                f"Invalid run_id format: {run_id!r}. Must match ^[A-Za-z0-9_-]{{1,64}}$"
            )

    def _run_file_path(self, run_id: str) -> Path:
        self._validate_run_id(run_id)
        if self.runs_dir is None:
            raise ValueError("runs_dir is not configured")
        target = (self.runs_dir / f"{run_id}.json").resolve()
        base = self.runs_dir.resolve()
        try:
            target.relative_to(base)
        except ValueError:
            raise ValueError(f"Path traversal detected for run_id: {run_id!r}")
        return target

    def init_store(self, runs_dir: Path | str) -> None:
        """Initialize the store with a specific runs_dir and load existing runs."""
        with self._lock:
            self.runs_dir = Path(runs_dir)
            self.runs_dir.mkdir(parents=True, exist_ok=True)
            self._load_from_disk_locked()

    def sweep_expired(self, current_time: float | None = None) -> int:
        """Remove any non-persisted run entries that have exceeded their TTL."""
        with self._lock:
            return self._sweep_expired_locked(current_time=current_time)

    def _sweep_expired_locked(self, current_time: float | None = None) -> int:
        now = time.time() if current_time is None else current_time
        expired_keys = [
            run_id
            for run_id, data in self._runs.items()
            if not data.get("persisted", False)
            and data.get("ttl") is not None
            and now - data.get("created_at", 0) > data["ttl"]
        ]
        for run_id in expired_keys:
            self._runs.pop(run_id, None)
        return len(expired_keys)

    def store_run(
        self,
        run_id: str,
        result: dict[str, Any],
        ttl: float | None = None,
        current_time: float | None = None,
        persist: bool = False,
    ) -> None:
        """
        Store task result for a given run_id.
        Validates run_id format and result type.
        If persist=True, writes atomically to <runs_dir>/<run_id>.json, has no TTL,
        and enforces the 200-run disk cap.
        If persist=False, stores in-memory with 1h TTL and bounds capacity to max_entries.
        """
        self._validate_run_id(run_id)
        if not isinstance(result, dict):
            raise ValueError(f"Invalid result: must be a dictionary, got {type(result).__name__}")

        now = time.time() if current_time is None else current_time

        with self._lock:
            if persist:
                if self.runs_dir is not None:
                    self.runs_dir.mkdir(parents=True, exist_ok=True)
                    file_path = self._run_file_path(run_id)
                    payload = {
                        "run_id": run_id,
                        "created_at": now,
                        "result": result,
                    }
                    temp_path = self.runs_dir / f"{run_id}.tmp.{os.getpid()}.{time.time_ns()}"
                    try:
                        with open(temp_path, "w", encoding="utf-8") as fh:
                            json.dump(payload, fh, indent=2, ensure_ascii=False)
                        os.replace(temp_path, file_path)
                    except Exception:
                        if temp_path.exists():
                            try:
                                temp_path.unlink()
                            except Exception:
                                pass
                        raise

                self._runs[run_id] = {
                    "result": result,
                    "created_at": now,
                    "ttl": None,
                    "persisted": True,
                }
                self._enforce_disk_cap_locked()
            else:
                self._sweep_expired_locked(current_time=current_time)

                if len(self._runs) >= self.max_entries and run_id not in self._runs:
                    non_persisted = [
                        k for k, v in self._runs.items() if not v.get("persisted", False)
                    ]
                    if non_persisted:
                        sorted_keys = sorted(
                            non_persisted, key=lambda k: self._runs[k].get("created_at", 0)
                        )
                        evict_count = max(1, len(sorted_keys) // 10)
                        for k in sorted_keys[:evict_count]:
                            self._runs.pop(k, None)

                entry_ttl = self.default_ttl if ttl is None else ttl
                self._runs[run_id] = {
                    "result": result,
                    "created_at": now,
                    "ttl": entry_ttl,
                    "persisted": False,
                }

    def get_run(self, run_id: str, current_time: float | None = None) -> dict[str, Any] | None:
        """
        Retrieve task result for a given run_id, or None if expired/not found.
        Performs format validation and TTL check.
        """
        if not isinstance(run_id, str) or not RUN_ID_REGEX.match(run_id):
            return None

        now = time.time() if current_time is None else current_time

        with self._lock:
            self._sweep_expired_locked(current_time=current_time)

            entry = self._runs.get(run_id)
            if entry:
                if not entry.get("persisted", False) and entry.get("ttl") is not None:
                    if now - entry.get("created_at", 0) > entry["ttl"]:
                        self._runs.pop(run_id, None)
                        return None
                return entry.get("result")

            # Fallback: check on disk if not in memory
            if self.runs_dir is not None:
                try:
                    file_path = self._run_file_path(run_id)
                    if file_path.is_file():
                        with open(file_path, "r", encoding="utf-8") as fh:
                            data = json.load(fh)
                        if isinstance(data, dict):
                            result = data.get("result")
                            if isinstance(result, dict):
                                created_at = float(data.get("created_at", file_path.stat().st_mtime))
                                self._runs[run_id] = {
                                    "result": result,
                                    "created_at": created_at,
                                    "ttl": None,
                                    "persisted": True,
                                }
                                return result
                except Exception:
                    pass

            return None

    def delete_run(self, run_id: str) -> bool:
        """Delete run from memory and disk. Returns True if existed and deleted, False otherwise."""
        if not isinstance(run_id, str) or not RUN_ID_REGEX.match(run_id):
            return False

        with self._lock:
            deleted = False
            if run_id in self._runs:
                self._runs.pop(run_id, None)
                deleted = True

            if self.runs_dir is not None:
                try:
                    file_path = self._run_file_path(run_id)
                    if file_path.is_file():
                        file_path.unlink()
                        deleted = True
                except Exception:
                    pass

            return deleted

    def _extract_summary(self, run_id: str, entry: dict[str, Any]) -> dict[str, Any]:
        created_at = float(entry.get("created_at", 0.0))
        result = entry.get("result") or {}

        status = result.get("status", "success" if result.get("torrent_path") else "unknown")
        task = result.get("task") or ("build" if result.get("torrent_path") or result.get("pack_title") else "unknown")

        if result.get("mode"):
            mode = result["mode"]
        elif result.get("single_scene") or result.get("task") == "BuildSingleScene":
            mode = "single_scene"
        else:
            mode = "megapack"

        pack_title = result.get("pack_title") or result.get("title") or result.get("name") or ""
        ready = bool(result.get("ready", status == "success"))
        torrent_path = result.get("torrent_path") or ""

        if isinstance(result.get("image_count"), int):
            image_count = result["image_count"]
        elif isinstance(result.get("contact_sheets"), list):
            image_count = len(result["contact_sheets"])
        elif isinstance(result.get("uploaded_urls"), list):
            image_count = len(result["uploaded_urls"])
        else:
            image_count = 0

        if isinstance(result.get("scene_count"), int):
            scene_count = result["scene_count"]
        elif isinstance(result.get("scenes"), list):
            scene_count = len(result["scenes"])
        elif mode == "single_scene":
            scene_count = 1
        else:
            scene_count = 0

        return {
            "run_id": run_id,
            "created_at": created_at,
            "status": status,
            "task": task,
            "mode": mode,
            "pack_title": pack_title,
            "title": pack_title,
            "ready": ready,
            "torrent_path": torrent_path,
            "image_count": image_count,
            "scene_count": scene_count,
        }

    def list_runs(self, limit: int = 50, include_non_persisted: bool = True) -> list[dict[str, Any]]:
        """Return run summaries sorted newest first by created_at."""
        with self._lock:
            candidates = [
                (run_id, data)
                for run_id, data in self._runs.items()
                if include_non_persisted or data.get("persisted", False)
            ]
            sorted_runs = sorted(
                candidates,
                key=lambda item: item[1].get("created_at", 0.0),
                reverse=True,
            )
            clamped_limit = max(1, min(limit, 200))
            return [
                self._extract_summary(run_id, data)
                for run_id, data in sorted_runs[:clamped_limit]
            ]

    def _enforce_disk_cap_locked(self) -> int:
        """Prune oldest persisted runs exceeding max_disk_runs (200)."""
        persisted = [
            (run_id, data)
            for run_id, data in self._runs.items()
            if data.get("persisted", False)
        ]
        if len(persisted) <= self.max_disk_runs:
            return 0

        sorted_persisted = sorted(
            persisted,
            key=lambda item: (item[1].get("created_at", 0.0), item[0]),
        )
        excess = len(sorted_persisted) - self.max_disk_runs
        pruned = 0
        for run_id, _ in sorted_persisted[:excess]:
            self._runs.pop(run_id, None)
            if self.runs_dir is not None:
                try:
                    fp = self._run_file_path(run_id)
                    if fp.is_file():
                        fp.unlink(missing_ok=True)
                        pruned += 1
                except Exception:
                    pass
        return pruned

    def load_from_disk(self, runs_dir: Path | str | None = None) -> int:
        """Load persisted runs from runs_dir into memory on startup and enforce cap."""
        with self._lock:
            if runs_dir is not None:
                self.runs_dir = Path(runs_dir)
            return self._load_from_disk_locked()

    def _load_from_disk_locked(self) -> int:
        if self.runs_dir is None:
            return 0
        if not self.runs_dir.exists():
            try:
                self.runs_dir.mkdir(parents=True, exist_ok=True)
            except Exception:
                return 0
            return 0

        count = 0
        try:
            files = list(self.runs_dir.glob("*.json"))
        except Exception:
            return 0

        for fp in files:
            run_id = fp.stem
            if not RUN_ID_REGEX.match(run_id):
                continue
            try:
                with open(fp, "r", encoding="utf-8") as fh:
                    data = json.load(fh)
                if not isinstance(data, dict):
                    continue
                result = data.get("result")
                if not isinstance(result, dict):
                    continue
                created_at = float(data.get("created_at", fp.stat().st_mtime))

                self._runs[run_id] = {
                    "result": result,
                    "created_at": created_at,
                    "ttl": None,
                    "persisted": True,
                }
                count += 1
            except Exception:
                continue

        self._enforce_disk_cap_locked()
        return count

    def clear(self, clear_disk: bool = True) -> None:
        """Clear stored run results from memory and disk (for testing)."""
        with self._lock:
            if clear_disk and self.runs_dir and self.runs_dir.exists():
                for fp in self.runs_dir.glob("*.json"):
                    try:
                        fp.unlink()
                    except Exception:
                        pass
            self._runs.clear()

    def __len__(self) -> int:
        with self._lock:
            return len(self._runs)


# Global singleton instance for the FastAPI backend service
run_store = RunStore(default_ttl=3600.0, max_entries=1000, max_disk_runs=200)
