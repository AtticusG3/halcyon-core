"""Media intent handlers for HALCYON."""
from __future__ import annotations

import json
from typing import Dict, List, Optional

try:  # pragma: no cover - optional redis dependency
    import redis
except Exception:  # pragma: no cover
    redis = None  # type: ignore

from orchestrator.logging.event_bus import EventBus
from services.media.overseerr_client import OverseerrClient
from services.media.recommender import MediaRecommender
from ha_adapter.intents.intent_router import IntentContext, IntentResult


class MediaIntentHandler:
    """Handle conversational media intents: recommend, request, watchlist."""

    def __init__(
        self,
        *,
        recommender: MediaRecommender,
        overseerr: OverseerrClient,
        event_bus: EventBus,
        redis_url: str = "redis://localhost:6379/0",
        cache_ttl: int = 900,
    ) -> None:
        self._recommender = recommender
        self._overseerr = overseerr
        self._event_bus = event_bus
        self._cache_ttl = cache_ttl
        self._redis = self._init_redis(redis_url)

    # ------------------------------------------------------------------
    def handle_recommend(self, ctx: IntentContext, slots: Dict[str, object]) -> IntentResult:
        options = self._recommender.recommend_for_user(ctx.speaker_uuid, k=3)
        if not options:
            return IntentResult(ok=False, spoken="I couldn't find anything suitable right now.")
        self._store_offers(ctx, options)
        spoken = self._recommender.format_spoken(options, ctx.persona)
        return IntentResult(
            ok=True,
            spoken=spoken,
            details={"options": options},
        )

    def handle_add_request(self, ctx: IntentContext, slots: Dict[str, object]) -> IntentResult:
        offers = self._load_offers(ctx)
        if not offers:
            return IntentResult(ok=False, spoken="I don't have a recommendation to act on yet.")
        pick = self._resolve_pick(slots)
        if pick is None or pick < 1 or pick > len(offers):
            return IntentResult(ok=False, spoken="Please choose one of the numbered options.")
        choice = offers[pick - 1]
        tmdb_id = choice.get("tmdb_id")
        if not tmdb_id:
            return IntentResult(ok=False, spoken="I don't have enough information to request that.")
        if not ctx.allow_sensitive and choice.get("adult"):
            return IntentResult(ok=False, spoken="That title isn't available right now.")
        try:
            result = self._overseerr.request(tmdb_id, choice.get("type", "movie"), user_note=None)
            ok = True
        except Exception as exc:  # pragma: no cover - defensive guard
            self._event_bus.publish(
                "media/error",
                {
                    "uuid": ctx.speaker_uuid,
                    "code": "overseerr_request_error",
                    "message": str(exc),
                },
            )
            return IntentResult(ok=False, spoken="I couldn't file that request.")
        self._event_bus.publish(
            "media/request",
            {
                "uuid": ctx.speaker_uuid,
                "tmdb_id": tmdb_id,
                "type": choice.get("type"),
                "title": choice.get("title"),
                "ok": True,
            },
        )
        spoken = "Request filed." if ctx.persona.upper() == "SCARLET" else "Added to your requests. I’ll notify you when it’s available."
        return IntentResult(ok=True, spoken=spoken, details={"request": result})

    def handle_add_to_list(self, ctx: IntentContext, slots: Dict[str, object]) -> IntentResult:
        offers = self._load_offers(ctx)
        if not offers:
            return IntentResult(ok=False, spoken="I don't have a recommendation to save yet.")
        pick = self._resolve_pick(slots)
        if pick is None or pick < 1 or pick > len(offers):
            return IntentResult(ok=False, spoken="Please choose one of the numbered options.")
        choice = offers[pick - 1]
        tmdb_id = choice.get("tmdb_id")
        if not tmdb_id:
            return IntentResult(ok=False, spoken="I don't have enough information to save that.")
        try:
            ok = self._overseerr.add_to_list(tmdb_id, list_name="watch-next")
        except Exception as exc:  # pragma: no cover - defensive guard
            self._event_bus.publish(
                "media/error",
                {
                    "uuid": ctx.speaker_uuid,
                    "code": "overseerr_add_list_error",
                    "message": str(exc),
                },
            )
            return IntentResult(ok=False, spoken="I couldn't add that to your list.")
        if not ok:
            return IntentResult(ok=False, spoken="I couldn't add that to your list.")
        spoken = "Added to your watchlist." if ctx.persona.upper() == "HALSTON" else "Added."
        return IntentResult(ok=True, spoken=spoken, details={"added": choice})

    # ------------------------------------------------------------------
    def _init_redis(self, redis_url: str):
        if redis is None:
            return None
        return redis.from_url(redis_url, decode_responses=True)

    def _store_offers(self, ctx: IntentContext, options: List[Dict[str, Any]]) -> None:
        if self._redis is None:
            return
        key = self._key(ctx)
        self._redis.set(key, json.dumps(options), ex=self._cache_ttl)

    def _load_offers(self, ctx: IntentContext) -> List[Dict[str, Any]]:
        if self._redis is None:
            return []
        raw = self._redis.get(self._key(ctx))
        if not raw:
            return []
        try:
            return json.loads(raw)
        except json.JSONDecodeError:
            return []

    def _key(self, ctx: IntentContext) -> str:
        if ctx.speaker_uuid:
            return f"halcyon:media:last:{ctx.speaker_uuid}"
        if ctx.session_id:
            return f"halcyon:media:last:session:{ctx.session_id}"
        return "halcyon:media:last:guest"

    def _resolve_pick(self, slots: Dict[str, object]) -> Optional[int]:
        pick = slots.get("pick")
        if isinstance(pick, int):
            return pick
        if isinstance(pick, str):
            mapping = {"first": 1, "second": 2, "third": 3}
            return mapping.get(pick.lower())
        return 1


__all__ = ["MediaIntentHandler"]
