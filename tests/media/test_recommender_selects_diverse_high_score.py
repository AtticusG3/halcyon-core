from __future__ import annotations

from typing import Any, Dict, List, Optional
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from services.media.recommender import MediaRecommender


class StubPlex:
    def __init__(self, history: List[Dict[str, Any]], continue_items: List[Dict[str, Any]]) -> None:
        self._history = history
        self._continue = continue_items

    def get_user_history(self, user_uuid: Optional[str], kind: str, limit: int = 200) -> List[Dict[str, Any]]:
        if user_uuid is None:
            return []
        return [item for item in self._history if item.get("type") == kind][:limit]

    def get_continue_watching(self, user_uuid: Optional[str], limit: int = 20) -> List[Dict[str, Any]]:
        if user_uuid is None:
            return []
        return self._continue[:limit]

    def get_library_stats(self, user_uuid: Optional[str]) -> Dict[str, Any]:
        return {"movies": 10, "shows": 5}


class StubTMDB:
    def __init__(self, trending_movies: List[Dict[str, Any]], trending_tv: List[Dict[str, Any]], related: Dict[int, List[Dict[str, Any]]]) -> None:
        self._trending_movies = trending_movies
        self._trending_tv = trending_tv
        self._related = related

    def trending(self, type: str, window: str = "week") -> List[Dict[str, Any]]:
        return self._trending_movies if type == "movie" else self._trending_tv

    def recommendations(self, tmdb_id: int, type: str) -> List[Dict[str, Any]]:
        return self._related.get(tmdb_id, [])


class RecordingBus:
    def __init__(self) -> None:
        self.messages: List[Dict[str, Any]] = []

    def publish(self, topic_suffix: str, payload: Dict[str, Any]) -> None:
        self.messages.append({"topic": topic_suffix, "payload": payload})


def test_recommender_returns_scored_options() -> None:
    history = [
        {
            "tmdb_id": 101,
            "type": "movie",
            "title": "Galactic Voyage",
            "genres": ["Sci-Fi"],
            "networks": ["Netflix"],
            "runtime": 120,
            "release_year": 2020,
        },
        {
            "tmdb_id": 202,
            "type": "tv",
            "title": "Mystery Manor",
            "genres": ["Mystery", "Drama"],
            "networks": ["HBO"],
            "runtime": 55,
            "release_year": 2019,
        },
    ]
    continue_items = [
        {
            "tmdb_id": 303,
            "type": "tv",
            "title": "Ongoing Thriller",
            "summary": "episode",
            "runtime": 50,
            "release_year": 2023,
            "genres": ["Thriller"],
            "in_progress": True,
        }
    ]
    trending_movies = [
        {"id": 404, "title": "Popular Space", "overview": "", "popularity": 20, "genre_ids": ["Sci-Fi"], "release_date": "2023-01-01"}
    ]
    trending_tv = [
        {"id": 505, "name": "Cozy Mystery", "overview": "", "popularity": 15, "genre_ids": ["Mystery"], "first_air_date": "2022-05-01"}
    ]
    related = {
        101: [
            {"id": 606, "title": "Space Sequel", "overview": "", "popularity": 8, "genre_ids": ["Sci-Fi"], "release_date": "2024-01-01"}
        ]
    }

    plex = StubPlex(history, continue_items)
    tmdb = StubTMDB(trending_movies, trending_tv, related)
    bus = RecordingBus()
    recommender = MediaRecommender(plex_client=plex, tmdb_client=tmdb, event_bus=bus)  # type: ignore[arg-type]

    options = recommender.recommend_for_user("user-123", k=3)
    assert len(options) == 3
    assert all("score" in option for option in options)
    assert any(option["source"] == "continue" for option in options)
    assert any(option["source"] == "trending" for option in options)
    assert any(option["source"] == "related" for option in options)
    assert bus.messages[-1]["topic"] == "media/recommendation"
    assert bus.messages[-1]["payload"]["n_options"] == 3

    spoken = recommender.format_spoken(options, persona="HALSTON")
    assert "Based on your recent habits" in spoken or "Here are three popular" in spoken
    assert "Which would you like" in spoken
