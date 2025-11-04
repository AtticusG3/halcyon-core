"""Overseerr API client for media requests and watchlists."""
from __future__ import annotations

from typing import Any, Dict, List, Literal, Optional

import requests


class OverseerrClient:
    """Interact with Overseerr's REST API."""

    def __init__(self, *, base_url: str, api_key: str, session: Optional[requests.Session] = None) -> None:
        if not base_url or not api_key:
            raise ValueError("OverseerrClient requires base_url and api_key")
        self._base_url = base_url.rstrip("/")
        self._api_key = api_key
        self._session = session or requests.Session()

    # ------------------------------------------------------------------
    def search(self, query: str, type: Literal["movie", "tv"]) -> List[Dict[str, Any]]:
        """Search Overseerr/TMDB for the provided query."""

        payload = self._request("GET", "/api/v1/search", params={"query": query, "type": type})
        results = payload.get("results", []) if isinstance(payload, dict) else []
        normalized: List[Dict[str, Any]] = []
        for item in results:
            tmdb_id = item.get("id")
            if tmdb_id is None:
                continue
            normalized.append(
                {
                    "tmdb_id": tmdb_id,
                    "type": item.get("mediaType", type),
                    "title": item.get("title") or item.get("name"),
                    "overview": item.get("overview"),
                    "poster": item.get("posterPath"),
                    "popularity": item.get("popularity", 0.0),
                }
            )
        return normalized

    def request(
        self,
        tmdb_id: int,
        type: Literal["movie", "tv"],
        *,
        user_note: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Submit a new media request."""

        body: Dict[str, Any] = {"mediaType": type, "mediaId": tmdb_id}
        if user_note:
            body["userNote"] = user_note
        return self._request("POST", "/api/v1/request", json=body)

    def add_to_list(self, tmdb_id: int, list_name: str = "watch-next") -> bool:
        """Add the provided TMDB title to a named Overseerr list."""

        response = self._request(
            "POST",
            f"/api/v1/list/{list_name}/items",
            json={"mediaId": tmdb_id, "mediaType": "movie"},
        )
        return bool(response)

    # ------------------------------------------------------------------
    def _request(
        self,
        method: Literal["GET", "POST", "DELETE", "PUT"],
        path: str,
        *,
        params: Optional[Dict[str, Any]] = None,
        json: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        url = f"{self._base_url}{path}"
        headers = {"X-Api-Key": self._api_key}
        response = self._session.request(
            method,
            url,
            params=params,
            json=json,
            headers=headers,
            timeout=10,
        )
        response.raise_for_status()
        if response.content:
            return response.json()
        return {}


__all__ = ["OverseerrClient"]
