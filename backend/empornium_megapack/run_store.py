"""
Run Store for the Empornium Megapack Builder.
Provides bounded in-memory storage for task run results, allowing the review UI
to poll and retrieve authoritative build results directly from the sidecar.
"""

from __future__ import annotations

import re
import time
from typing import Any

RUN_ID_REGEX = re.compile(r"^[A-Za-z0-9_-]{1,64}$")


class RunStore:
    def __init__(self, default_ttl: float = 3600.0, max_entries: int = 1000):
        self._runs: dict[str, dict[str, Any]] = {}
        self.default_ttl = default_ttl
        self.max_entries = max_entries

    def sweep_expired(self, current_time: float | None = None) -> int:
        """Remove any run entries that have exceeded their TTL."""
        now = time.time() if current_time is None else current_time
        expired_keys = [
            run_id
            for run_id, data in self._runs.items()
            if now - data.get("created_at", 0) > data.get("ttl", self.default_ttl)
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
    ) -> None:
        """
        Store task result for a given run_id.
        Validates run_id format and result type.
        Enforces max_entries by evicting oldest entries if necessary.
        """
        if not isinstance(run_id, str) or not RUN_ID_REGEX.match(run_id):
            raise ValueError(f"Invalid run_id format: {run_id!r}. Must match ^[A-Za-z0-9_-]{{1,64}}$")
        if not isinstance(result, dict):
            raise ValueError(f"Invalid result: must be a dictionary, got {type(result).__name__}")

        self.sweep_expired(current_time=current_time)

        if len(self._runs) >= self.max_entries and run_id not in self._runs:
            sorted_keys = sorted(self._runs.keys(), key=lambda k: self._runs[k].get("created_at", 0))
            evict_count = max(1, len(sorted_keys) // 10)
            for k in sorted_keys[:evict_count]:
                self._runs.pop(k, None)

        now = time.time() if current_time is None else current_time
        entry_ttl = self.default_ttl if ttl is None else ttl

        self._runs[run_id] = {
            "result": result,
            "created_at": now,
            "ttl": entry_ttl,
        }

    def get_run(self, run_id: str, current_time: float | None = None) -> dict[str, Any] | None:
        """
        Retrieve task result for a given run_id, or None if expired/not found.
        Performs format validation and TTL check.
        """
        if not isinstance(run_id, str) or not RUN_ID_REGEX.match(run_id):
            return None

        self.sweep_expired(current_time=current_time)

        entry = self._runs.get(run_id)
        if not entry:
            return None

        now = time.time() if current_time is None else current_time
        if now - entry.get("created_at", 0) > entry.get("ttl", self.default_ttl):
            self._runs.pop(run_id, None)
            return None

        return entry.get("result")

    def clear(self) -> None:
        """Clear all stored run results (used in testing)."""
        self._runs.clear()

    def __len__(self) -> int:
        return len(self._runs)


# Global singleton instance for the FastAPI backend service
run_store = RunStore(default_ttl=3600.0, max_entries=1000)
