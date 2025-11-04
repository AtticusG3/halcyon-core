"""Minimal TMDB API helper for metadata enrichment."""
from __future__ import annotations

import json
import time
from dataclasses import dataclass
from typing import Any, Dict, List, Literal, Optional, Tuple

import requests


@dataclass(slots=True)
class _CacheEntry:
    value: Any
    expires_at: float


class TMDBClient:
    """Fetch metadata, trending lists, and recommendations from TMDB."""

    BASE_URL = "https://api.themoviedb.org/3"

    def __init__(
        self,
        *,
        api_key: str,
        session: Optional[requests.Session] = None,
        cache_ttl: int = 600,
    ) -> None:
        if not api_key:
            raise ValueError("TMDBClient requires an API key")
        self._api_key = api_key
        self._session = session or requests.Session()
        self._cache_ttl = cache_ttl
        self._cache: Dict[Tuple[str, Tuple[Tuple[str, Any], ...]], _CacheEntry] = {}

    # ------------------------------------------------------------------
    def details(self, tmdb_id: int, type: Literal["movie", "tv"]) -> Dict[str, Any]:
        return self._get_json(f"/{type}/{tmdb_id}")

    def trending(self, type: Literal["movie", "tv"], window: Literal["day", "week"] = "week") -> List[Dict[str, Any]]:
        payload = self._get_json(f"/trending/{type}/{window}")
        return payload.get("results", []) if isinstance(payload, dict) else []

    def recommendations(self, tmdb_id: int, type: Literal["movie", "tv"]) -> List[Dict[str, Any]]:
        payload = self._get_json(f"/{type}/{tmdb_id}/recommendations")
        return payload.get("results", []) if isinstance(payload, dict) else []

    # ------------------------------------------------------------------
    def _get_json(self, path: str, params: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        cache_key = self._cache_key(path, params or {})
        cached = self._cache.get(cache_key)
        now = time.time()
        if cached and cached.expires_at > now:
            return cached.value
        url = f"{self.BASE_URL}{path}"
        query = dict(params or {})
        query.setdefault("api_key", self._api_key)
        response = self._session.get(url, params=query, timeout=8)
        response.raise_for_status()
        data = response.json() if response.content else {}
        self._cache[cache_key] = _CacheEntry(value=data, expires_at=now + self._cache_ttl)
        return data

    def _cache_key(self, path: str, params: Dict[str, Any]) -> Tuple[str, Tuple[Tuple[str, Any], ...]]:
        key_items = tuple(sorted(params.items()))
        return path, key_items


__all__ = ["TMDBClient"]
