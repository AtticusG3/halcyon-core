"""Redis-backed session persistence for HALCYON orchestrator."""
from __future__ import annotations

import json
import time
from dataclasses import asdict, dataclass
from typing import Optional

import redis


@dataclass
class SessionState:
    """Serializable representation of a speaker session."""

    speaker_uuid: Optional[str]
    last_trust: float = 0.0
    last_persona: str = "HALSTON"
    last_seen_ts: float = 0.0
    conversation_turn: int = 0
    context_mode: str = "home"
    voice_confidence: Optional[float] = None
    face_confidence: Optional[float] = None
    reassurance: float = 0.0
    threat: float = 0.0
    last_intent: Optional[str] = None
    last_response: Optional[str] = None


class SessionStore:
    """Redis-backed shared session cache.

    The store keeps session state shared across microphones and devices so
    persona and trust hysteresis remain stable within a household.
    """

    def __init__(self, redis_url: str = "redis://localhost:6379/0", ttl_seconds: int = 3600) -> None:
        self._redis = redis.from_url(redis_url, decode_responses=True)
        self._ttl = ttl_seconds

    def _key(self, speaker_uuid: Optional[str], temp_id: str) -> str:
        if speaker_uuid:
            return f"halcyon:session:{speaker_uuid}"
        return f"halcyon:session:guest:{temp_id}"

    def load(self, speaker_uuid: Optional[str], temp_id: str) -> SessionState:
        key = self._key(speaker_uuid, temp_id)
        raw = self._redis.get(key)
        if raw is None:
            return SessionState(speaker_uuid=speaker_uuid, last_seen_ts=time.time())
        data = json.loads(raw)
        return SessionState(**data)

    def save(self, state: SessionState, speaker_uuid: Optional[str], temp_id: str) -> None:
        key = self._key(speaker_uuid, temp_id)
        state.speaker_uuid = speaker_uuid
        state.last_seen_ts = time.time()
        payload = json.dumps(asdict(state))
        self._redis.set(key, payload, ex=self._ttl)

    def touch_context(self, speaker_uuid: Optional[str], temp_id: str, context_mode: str) -> None:
        state = self.load(speaker_uuid, temp_id)
        state.context_mode = context_mode
        self.save(state, speaker_uuid, temp_id)

    def clear(self, speaker_uuid: Optional[str], temp_id: str) -> None:
        key = self._key(speaker_uuid, temp_id)
        try:
            self._redis.delete(key)
        except AttributeError:
            # Not all redis clients expose delete (our in-repo stub does).
            pass
