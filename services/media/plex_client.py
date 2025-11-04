"""Plex API client with household-aware caching."""
from __future__ import annotations

import json
import time
from dataclasses import dataclass
from typing import Any, Dict, List, Literal, Optional, Tuple

import requests

try:  # pragma: no cover - optional redis dependency
    import redis
except Exception:  # pragma: no cover - fallback for tests
    redis = None  # type: ignore

HistoryKind = Literal["movie", "show"]


@dataclass(slots=True)
class _CacheEntry:
    value: List[Dict[str, Any]]
    expires_at: float


class PlexClient:
    """Thin Plex API wrapper focused on watch history and discovery."""

    def __init__(
        self,
        *,
        base_url: str,
        token: str,
        library_movies_section: str = "Movies",
        library_tv_section: str = "TV Shows",
        user_name: Optional[str] = None,
        cache_ttl: int = 300,
        redis_url: Optional[str] = None,
        session: Optional[requests.Session] = None,
    ) -> None:
        if not base_url or not token:
            raise ValueError("PlexClient requires base_url and token")
        self._base_url = base_url.rstrip("/")
        self._token = token
        self._session = session or requests.Session()
        self._library_movies = library_movies_section
        self._library_tv = library_tv_section
        self._user_name = user_name
        self._cache_ttl = cache_ttl
        self._cache: Dict[Tuple[str, str, int], _CacheEntry] = {}
        self._redis = self._init_redis(redis_url)

    # ------------------------------------------------------------------
    def get_user_history(
        self,
        user_uuid: Optional[str],
        kind: HistoryKind,
        *,
        limit: int = 200,
    ) -> List[Dict[str, Any]]:
        """Return the most recent watch history for ``user_uuid``."""

        if user_uuid is None:
            # Guests do not receive personalized history for privacy reasons.
            return []

        cache_key = ("history", f"{user_uuid}:{kind}", limit)
        cached = self._get_cached(cache_key)
        if cached is not None:
            return cached

        payload = self._request_json(
            "/status/sessions/history/all",
            params={
                "type": 1 if kind == "movie" else 2,
                "X-Plex-Token": self._token,
                "accountID": self._user_name,
                "max": limit,
                "json": 1,
            },
        )
        entries = self._parse_history(payload)
        filtered = [entry for entry in entries if self._entry_visible_to(entry, user_uuid)]
        result = filtered[:limit]
        self._set_cached(cache_key, result)
        return result

    def get_continue_watching(
        self,
        user_uuid: Optional[str],
        *,
        limit: int = 20,
    ) -> List[Dict[str, Any]]:
        """Return items the user is currently watching."""

        if user_uuid is None:
            return []

        cache_key = ("continue", user_uuid, limit)
        cached = self._get_cached(cache_key)
        if cached is not None:
            return cached

        payload = self._request_json(
            "/status/sessions/history/all",
            params={
                "X-Plex-Token": self._token,
                "accountID": self._user_name,
                "inProgress": 1,
                "max": limit,
                "json": 1,
            },
        )
        entries = [entry for entry in self._parse_history(payload) if entry.get("in_progress")]
        self._set_cached(cache_key, entries[:limit])
        return entries[:limit]

    def get_library_stats(self, user_uuid: Optional[str]) -> Dict[str, Any]:
        """Return aggregated library metadata used for recommendations."""

        if user_uuid is None:
            return {"movies": 0, "shows": 0}

        cache_key = ("library", user_uuid, 0)
        cached = self._get_cached(cache_key)
        if cached is not None:
            return cached[0] if cached else {"movies": 0, "shows": 0}

        payload = self._request_json(
            "/library/sections",
            params={"X-Plex-Token": self._token, "json": 1},
        )
        stats = {"movies": 0, "shows": 0}
        container = payload.get("MediaContainer", {}) if isinstance(payload, dict) else {}
        sections = container.get("Directory", [])
        for item in sections:
            title = item.get("title")
            size = int(item.get("size", 0))
            if title == self._library_movies:
                stats["movies"] = size
            elif title == self._library_tv:
                stats["shows"] = size
        self._set_cached(cache_key, [stats])
        return stats

    # ------------------------------------------------------------------
    def _init_redis(self, redis_url: Optional[str]):
        if redis_url and redis is not None:
            return redis.from_url(redis_url, decode_responses=True)
        return None

    def _cache_key(self, key: Tuple[str, str, int]) -> str:
        return f"halcyon:plex:{key[0]}:{key[1]}:{key[2]}"

    def _get_cached(self, key: Tuple[str, str, int]) -> Optional[List[Dict[str, Any]]]:
        now = time.time()
        entry = self._cache.get(key)
        if entry and entry.expires_at > now:
            return entry.value
        if self._redis is not None:
            raw = self._redis.get(self._cache_key(key))
            if raw is not None:
                try:
                    data = json.loads(raw)
                    self._cache[key] = _CacheEntry(value=data, expires_at=now + self._cache_ttl)
                    return data
                except json.JSONDecodeError:
                    return None
        return None

    def _set_cached(self, key: Tuple[str, str, int], value: List[Dict[str, Any]]) -> None:
        expires = time.time() + self._cache_ttl
        self._cache[key] = _CacheEntry(value=value, expires_at=expires)
        if self._redis is not None:
            self._redis.set(self._cache_key(key), json.dumps(value), ex=self._cache_ttl)

    def _request_json(self, path: str, params: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        url = f"{self._base_url}{path}"
        query = dict(params or {})
        query.setdefault("X-Plex-Token", self._token)
        response = self._session.get(url, params=query, timeout=8)
        response.raise_for_status()
        if "application/json" in response.headers.get("Content-Type", ""):
            return response.json()
        # Some Plex endpoints return XML by default; fall back to naive JSON parsing if possible.
        try:
            return response.json()
        except ValueError:  # pragma: no cover - best effort fallback
            return {}

    def _parse_history(self, payload: Dict[str, Any]) -> List[Dict[str, Any]]:
        container = payload.get("MediaContainer", {}) if isinstance(payload, dict) else {}
        metadata = container.get("Metadata", [])
        results: List[Dict[str, Any]] = []
        for item in metadata or []:
            entry = self._normalize_entry(item)
            if entry:
                results.append(entry)
        return results

    def _normalize_entry(self, item: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        title = item.get("title")
        if not title:
            return None
        media_type = "movie" if item.get("type") == "movie" else "show"
        tmdb_id = self._extract_tmdb_id(item)
        genres = [genre.get("tag") for genre in item.get("Genre", []) if genre.get("tag")]
        networks = [studio.get("tag") for studio in item.get("Studio", []) if studio.get("tag")]
        watchers = self._extract_watchers(item)
        return {
            "title": title,
            "type": media_type,
            "tmdb_id": tmdb_id,
            "rating_key": item.get("ratingKey"),
            "summary": item.get("summary"),
            "genres": genres,
            "networks": networks,
            "runtime": self._normalize_duration(item.get("duration")),
            "release_year": item.get("year"),
            "watched_at": item.get("viewedAt"),
            "in_progress": bool(item.get("viewOffset")),
            "watchers": watchers,
            "source": "plex",
        }

    def _normalize_duration(self, duration_ms: Any) -> Optional[int]:
        try:
            ms = int(duration_ms)
        except (TypeError, ValueError):
            return None
        return ms // 60000 or None

    def _extract_tmdb_id(self, item: Dict[str, Any]) -> Optional[int]:
        guids = item.get("Guid", [])
        for guid in guids:
            id_str = guid.get("id")
            if not id_str:
                continue
            if "tmdb" in id_str:
                try:
                    return int(id_str.rsplit("//", 1)[-1])
                except ValueError:
                    continue
        return None

    def _extract_watchers(self, item: Dict[str, Any]) -> List[str]:
        watchers: List[str] = []
        accounts = item.get("Account")
        if isinstance(accounts, list):
            for account in accounts:
                uuid = account.get("uuid") or account.get("id")
                if uuid:
                    watchers.append(str(uuid))
        elif isinstance(accounts, dict):
            uuid = accounts.get("uuid") or accounts.get("id")
            if uuid:
                watchers.append(str(uuid))
        custom = item.get("User")
        if isinstance(custom, dict):
            uuid = custom.get("uuid") or custom.get("id")
            if uuid:
                watchers.append(str(uuid))
        return watchers

    def _entry_visible_to(self, entry: Dict[str, Any], user_uuid: str) -> bool:
        watchers = entry.get("watchers") or []
        if watchers:
            return user_uuid in {str(w) for w in watchers}
        # Fallback to redis mapping keyed by rating key
        if self._redis is not None:
            rating_key = entry.get("rating_key")
            if rating_key:
                mapping_key = f"halcyon:plex:watched:{rating_key}"
                stored = self._redis.get(mapping_key)
                if stored:
                    try:
                        users = json.loads(stored)
                        return user_uuid in users
                    except json.JSONDecodeError:
                        return False
        return False


__all__ = ["PlexClient"]
