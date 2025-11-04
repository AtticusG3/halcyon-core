from __future__ import annotations

from typing import Any, Dict, List, Optional
from uuid import uuid4

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from ha_adapter.intents.intent_media import MediaIntentHandler
from ha_adapter.intents.intent_router import IntentContext


class StubRecommender:
    def __init__(self, options: List[Dict[str, Any]]) -> None:
        self._options = options

    def recommend_for_user(self, user_uuid: Optional[str], k: int = 3) -> List[Dict[str, Any]]:
        return self._options[:k]

    def format_spoken(self, options: List[Dict[str, Any]], persona: str) -> str:
        return "Test spoken output"


class StubOverseerr:
    def __init__(self) -> None:
        self.requests: List[Dict[str, Any]] = []
        self.lists: List[Dict[str, Any]] = []

    def request(self, tmdb_id: int, type: str, user_note: Optional[str] = None) -> Dict[str, Any]:
        payload = {"tmdb_id": tmdb_id, "type": type, "user_note": user_note}
        self.requests.append(payload)
        return payload

    def add_to_list(self, tmdb_id: int, list_name: str = "watch-next") -> bool:
        self.lists.append({"tmdb_id": tmdb_id, "list": list_name})
        return True


class RecordingBus:
    def __init__(self) -> None:
        self.messages: List[Dict[str, Any]] = []

    def publish(self, topic_suffix: str, payload: Dict[str, Any]) -> None:
        self.messages.append({"topic": topic_suffix, "payload": payload})


def make_context(uuid: Optional[str]) -> IntentContext:
    return IntentContext(role="owner", allow_sensitive=True, mode="home", speaker_uuid=uuid, session_id="sess", persona="HALSTON")


def test_handle_recommend_and_request_flow() -> None:
    options = [
        {"tmdb_id": 1001, "type": "movie", "title": "Option A"},
        {"tmdb_id": 2002, "type": "tv", "title": "Option B"},
    ]
    recommender = StubRecommender(options)
    overseerr = StubOverseerr()
    bus = RecordingBus()
    handler = MediaIntentHandler(
        recommender=recommender,  # type: ignore[arg-type]
        overseerr=overseerr,      # type: ignore[arg-type]
        event_bus=bus,            # type: ignore[arg-type]
        redis_url=f"memory://{uuid4()}",
    )

    ctx = make_context("user-abc")
    recommend_result = handler.handle_recommend(ctx, {})
    assert recommend_result.ok

    request_result = handler.handle_add_request(ctx, {"pick": 1})
    assert request_result.ok
    assert overseerr.requests[-1]["tmdb_id"] == 1001
    assert bus.messages[-1]["topic"] == "media/request"

    list_result = handler.handle_add_to_list(ctx, {"pick": 2})
    assert list_result.ok
    assert overseerr.lists[-1]["tmdb_id"] == 2002


def test_guest_without_history_cannot_request_without_offer() -> None:
    recommender = StubRecommender([])
    overseerr = StubOverseerr()
    bus = RecordingBus()
    handler = MediaIntentHandler(
        recommender=recommender,  # type: ignore[arg-type]
        overseerr=overseerr,      # type: ignore[arg-type]
        event_bus=bus,            # type: ignore[arg-type]
        redis_url=f"memory://{uuid4()}",
    )

    guest_ctx = make_context(None)
    request_result = handler.handle_add_request(guest_ctx, {"pick": 1})
    assert not request_result.ok
    assert "recommendation" in request_result.spoken.lower()
