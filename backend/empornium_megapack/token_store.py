"""
Token Store for the Empornium Megapack Builder.
Provides short-lived token storage for transport of scene ID arrays to iframe review UI,
avoiding browser URL length limits and iframe blocked rendering for large scene selections (e.g. 66+ scenes).
"""

import time
import uuid
from typing import Any


class TokenStore:
    def __init__(self, default_ttl: float = 3600.0):
        self._tokens: dict[str, dict[str, Any]] = {}
        self._ip_create_timestamps: dict[str, list[float]] = {}
        self.default_ttl = default_ttl


    def sweep_expired(self, current_time: float | None = None) -> int:
        """Remove any tokens that have exceeded their TTL."""
        now = time.time() if current_time is None else current_time
        expired_keys = [
            token
            for token, data in self._tokens.items()
            if now - data.get("created_at", 0) > data.get("ttl", self.default_ttl)
        ]
        for token in expired_keys:
            del self._tokens[token]
        return len(expired_keys)

    def create_token(
        self, scene_ids: list[int], ttl: float | None = None, current_time: float | None = None
    ) -> str:
        """
        Validate and store a list of scene IDs, returning a uuid4 token string.
        Each scene ID must be an integer > 0.
        """
        if not isinstance(scene_ids, (list, tuple)) or len(scene_ids) == 0:
            raise ValueError("sceneIds must be a non-empty list of positive integers")
        if len(scene_ids) > 200:
            raise ValueError("too many scene IDs (max 200)")

        validated_ids: list[int] = []
        for sid in scene_ids:
            # Note: bool is an instance of int in Python (isinstance(True, int) is True)
            if isinstance(sid, bool) or not isinstance(sid, int) or sid <= 0:
                raise ValueError(f"Invalid scene ID: {sid!r}. Must be a positive integer.")
            validated_ids.append(int(sid))

        self.sweep_expired(current_time=current_time)

        token = uuid.uuid4().hex
        now = time.time() if current_time is None else current_time
        token_ttl = self.default_ttl if ttl is None else ttl

        self._tokens[token] = {
            "sceneIds": validated_ids,
            "created_at": now,
            "ttl": token_ttl,
        }
        return token

    def get_token(self, token: str, current_time: float | None = None) -> list[int] | None:
        """
        Retrieve scene IDs for a given token, or None if expired/not found.
        Performs format validation and TTL sweep on read.
        """
        if not token or not isinstance(token, str) or len(token) > 64 or not token.isalnum():
            return None

        self.sweep_expired(current_time=current_time)


        entry = self._tokens.get(token)
        if not entry:
            return None

        now = time.time() if current_time is None else current_time
        if now - entry.get("created_at", 0) > entry.get("ttl", self.default_ttl):
            self._tokens.pop(token, None)
            return None

        return list(entry["sceneIds"])

    def check_rate_limit(
        self,
        client_ip: str | None = None,
        max_requests: int = 10,
        window_seconds: float = 60.0,
        current_time: float | None = None,
    ) -> bool:
        """
        Check if create_token calls from the given client IP (or global)
        exceed max_requests within window_seconds.
        Returns True if allowed, False if rate limited.
        """
        now = time.time() if current_time is None else current_time
        key = client_ip or "global"
        timestamps = self._ip_create_timestamps.setdefault(key, [])
        cutoff = now - window_seconds
        valid_timestamps = [t for t in timestamps if t > cutoff]
        if len(valid_timestamps) >= max_requests:
            self._ip_create_timestamps[key] = valid_timestamps
            return False
        valid_timestamps.append(now)
        self._ip_create_timestamps[key] = valid_timestamps
        return True

    def clear(self) -> None:
        """Clear all tokens and rate limit records (used in testing)."""
        self._tokens.clear()
        self._ip_create_timestamps.clear()

    def __len__(self) -> int:
        return len(self._tokens)



# Global singleton instance for the FastAPI backend service
token_store = TokenStore(default_ttl=3600.0)
