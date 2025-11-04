from __future__ import annotations

from typing import Any, Dict, List, Optional

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from services.media.recommender import MediaRecommender


class StubPlex:
    def get_user_history(self, user_uuid: Optional[str], kind: str, limit: int = 200) -> List[Dict[str, Any]]:
        return []

    def get_continue_watching(self, user_uuid: Optional[str], limit: int = 20) -> List[Dict[str, Any]]:
        return []

    def get_library_stats(self, user_uuid: Optional[str]) -> Dict[str, Any]:
        return {"movies": 0, "shows": 0}


class StubTMDB:
    def __init__(self, trending: List[Dict[str, Any]]) -> None:
        self._trending = trending

    def trending(self, type: str, window: str = "week") -> List[Dict[str, Any]]:
        return self._trending

    def recommendations(self, tmdb_id: int, type: str) -> List[Dict[str, Any]]:
        return []


class RecordingBus:
    def __init__(self) -> None:
        self.messages: List[Dict[str, Any]] = []

    def publish(self, topic_suffix: str, payload: Dict[str, Any]) -> None:
        self.messages.append({"topic": topic_suffix, "payload": payload})


def test_guest_recommendations_use_trending_only() -> None:
    trending_payload = [
        {"id": 1, "title": "Popular Movie", "overview": "", "popularity": 50, "genre_ids": ["Action"], "release_date": "2023-01-01"},
        {"id": 2, "title": "Popular Movie 2", "overview": "", "popularity": 45, "genre_ids": ["Comedy"], "release_date": "2023-02-01"},
        {"id": 3, "title": "Popular Movie 3", "overview": "", "popularity": 40, "genre_ids": ["Drama"], "release_date": "2023-03-01"},
    ]
    recommender = MediaRecommender(
        plex_client=StubPlex(),  # type: ignore[arg-type]
        tmdb_client=StubTMDB(trending_payload),  # type: ignore[arg-type]
        event_bus=RecordingBus(),  # type: ignore[arg-type]
    )

    options = recommender.recommend_for_user(None, k=3)
    assert len(options) == 3
    assert all(option.get("source") == "trending" for option in options)
    assert not any(option.get("personalized") for option in options)

    spoken = recommender.format_spoken(options, persona="HALSTON")
    assert "popular options" in spoken
