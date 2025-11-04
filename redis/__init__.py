"""In-repo Redis shim for HALCYON tests."""
from __future__ import annotations

import threading
import time
from typing import Dict, Optional, Tuple


class _InMemoryRedis:
    def __init__(self) -> None:
        self._data: Dict[str, Tuple[str, Optional[float]]] = {}
        self._lock = threading.RLock()

    def get(self, key: str) -> Optional[str]:
        with self._lock:
            value = self._data.get(key)
            if value is None:
                return None
            payload, expires_at = value
            if expires_at is not None and expires_at < time.time():
                self._data.pop(key, None)
                return None
            return payload

    def set(self, key: str, value: str, ex: Optional[int] = None) -> None:
        with self._lock:
            expiry = time.time() + ex if ex else None
            self._data[key] = (value, expiry)

    def delete(self, key: str) -> None:
        with self._lock:
            self._data.pop(key, None)


_instances: Dict[str, _InMemoryRedis] = {}
_instances_lock = threading.RLock()


def from_url(url: str, *, decode_responses: bool = False):  # pragma: no cover - trivial
    """Return a shared in-memory Redis instance keyed by URL."""

    with _instances_lock:
        instance = _instances.get(url)
        if instance is None:
            instance = _InMemoryRedis()
            _instances[url] = instance
        return instance
