"""Conversation routing and room selection for multi-room voice pipeline."""
from __future__ import annotations

import os
import time
from typing import Dict, List, Optional, Tuple

import redis

from orchestrator.logging.event_bus import EventBus
from services.voice_pipeline.room_registry import RoomRegistry


class ConversationRouter:
    """Routes conversations to appropriate rooms with follow-me handoff support."""

    def __init__(
        self,
        room_registry: RoomRegistry,
        *,
        event_bus: Optional[EventBus] = None,
        redis_url: str = "redis://localhost:6379/0",
        follow_me_max_gap_sec: Optional[float] = None,
        handoff_min_confidence: Optional[float] = None,
    ) -> None:
        """Initialize the conversation router.

        Parameters
        ----------
        room_registry:
            RoomRegistry instance for room configuration.
        event_bus:
            EventBus instance for MQTT diagnostics. If None, creates a default one.
        redis_url:
            Redis connection URL for storing room state.
        follow_me_max_gap_sec:
            Maximum seconds between wakewords for follow-me handoff.
            If None, reads from FOLLOW_ME_MAX_GAP_SEC environment variable (default 10.0).
        handoff_min_confidence:
            Minimum confidence for handoff. If None, reads from HANDOFF_MIN_CONFIDENCE
            environment variable (default 0.75).
        """
        self._room_registry = room_registry
        self._event_bus = event_bus or EventBus()
        self._redis = redis.from_url(redis_url, decode_responses=True)

        gap_env = follow_me_max_gap_sec
        if gap_env is None:
            gap_env = float(os.getenv("FOLLOW_ME_MAX_GAP_SEC", "10.0"))
        self._follow_me_max_gap = gap_env

        conf_env = handoff_min_confidence
        if conf_env is None:
            conf_env = float(os.getenv("HANDOFF_MIN_CONFIDENCE", "0.75"))
        self._handoff_min_confidence = conf_env

    def _key_last_room(self, uuid: str) -> str:
        """Redis key for last room used by a speaker."""
        return f"halcyon:voice:last_room:{uuid}"

    def _key_last_seen(self, uuid: str) -> str:
        """Redis key for last seen timestamp."""
        return f"halcyon:voice:last_seen:{uuid}"

    def _key_room_lock(self, uuid: str) -> str:
        """Redis key for manual room lock."""
        return f"halcyon:voice:room_lock:{uuid}"

    def select_active_room(
        self,
        uuid: Optional[str],
        temp_id: str,
        last_room_hint: Optional[str] = None,
    ) -> str:
        """Select the active room for a conversation.

        Parameters
        ----------
        uuid:
            Stable speaker UUID (if known).
        temp_id:
            Temporary speaker identifier (e.g., "mic:lounge_1:ts").
        last_room_hint:
            Room where wakeword was detected (preferred if recent).

        Returns
        -------
        Room ID for the active conversation.
        """
        now = time.time()

        # Check for manual room lock
        if uuid:
            lock_key = self._key_room_lock(uuid)
            locked_room = self._redis.get(lock_key)
            if locked_room:
                return locked_room

        # Prefer last_room_hint if provided and recent
        if last_room_hint:
            room = self._room_registry.get_room(last_room_hint)
            if room:
                # Update last room and timestamp
                if uuid:
                    self._redis.set(self._key_last_room(uuid), last_room_hint, ex=3600)
                    self._redis.set(self._key_last_seen(uuid), str(now), ex=3600)
                return last_room_hint

        # Fall back to last room from Redis
        if uuid:
            last_room = self._redis.get(self._key_last_room(uuid))
            if last_room:
                room = self._room_registry.get_room(last_room)
                if room:
                    return last_room

        # Default to default room or first available
        default = self._room_registry.get_default_room()
        if default:
            return default

        rooms = self._room_registry.list_rooms()
        if rooms:
            return rooms[0]["id"]

        raise RuntimeError("No rooms configured")

    def follow_me(
        self,
        uuid: Optional[str],
        candidate_rooms: List[Tuple[str, float]],
    ) -> Optional[str]:
        """Attempt follow-me handoff to a new room.

        Parameters
        ----------
        uuid:
            Stable speaker UUID.
        candidate_rooms:
            List of (room_id, confidence) tuples from voice matching.

        Returns
        -------
        Room ID if handoff should occur, None otherwise.
        """
        if not uuid or not candidate_rooms:
            return None

        now = time.time()

        # Get last seen timestamp
        last_seen_key = self._key_last_seen(uuid)
        last_seen_raw = self._redis.get(last_seen_key)
        if not last_seen_raw:
            return None

        try:
            last_seen = float(last_seen_raw)
        except (ValueError, TypeError):
            return None

        # Check if within follow-me window
        gap = now - last_seen
        if gap > self._follow_me_max_gap:
            return None

        # Get last room
        last_room_key = self._key_last_room(uuid)
        last_room = self._redis.get(last_room_key)
        if not last_room:
            return None

        # Find best candidate room (highest confidence, not last room)
        best_room = None
        best_conf = 0.0
        for room_id, confidence in candidate_rooms:
            if room_id != last_room and confidence >= self._handoff_min_confidence:
                if confidence > best_conf:
                    best_conf = confidence
                    best_room = room_id

        if best_room:
            # Update state
            self._redis.set(self._key_last_room(uuid), best_room, ex=3600)
            self._redis.set(self._key_last_seen(uuid), str(now), ex=3600)

            # Publish handoff event
            self._event_bus.publish(
                "voice/handoff",
                {
                    "uuid": uuid,
                    "from": last_room,
                    "to": best_room,
                    "confidence": round(best_conf, 3),
                },
            )
            return best_room

        return None

    def can_speak_in(self, room_id: str, persona: str = "HALSTON") -> bool:
        """Check if speech output is allowed in a room.

        Parameters
        ----------
        room_id:
            Room identifier.
        persona:
            Persona name ("HALSTON" or "SCARLET"). SCARLET can override DND
            for critical announcements.

        Returns
        -------
        True if speech is allowed, False otherwise.
        """
        # Privacy zones always deny speech
        if self._room_registry.is_privacy_zone(room_id):
            return False

        # DND zones deny speech unless SCARLET critical
        if self._room_registry.is_dnd_zone(room_id):
            # SCARLET can override DND for critical announcements
            # (This is a simplified check; actual implementation might check
            # for specific intent types or threat levels)
            return persona == "SCARLET"

        return True

    def route_tts(self, room_id: str, wav_bytes: bytes) -> bool:
        """Route TTS audio to a room (placeholder for OutputRouter integration).

        This method is a placeholder. Actual routing should be done via OutputRouter.

        Parameters
        ----------
        room_id:
            Target room identifier.
        wav_bytes:
            WAV audio bytes to route.

        Returns
        -------
        True if routing succeeded, False otherwise.
        """
        # Placeholder: actual implementation in OutputRouter
        return False

    def set_room_lock(self, uuid: str, room_id: Optional[str]) -> None:
        """Manually lock a speaker to a specific room.

        Parameters
        ----------
        uuid:
            Speaker UUID.
        room_id:
            Room ID to lock to, or None to unlock.
        """
        lock_key = self._key_room_lock(uuid)
        if room_id:
            self._redis.set(lock_key, room_id, ex=3600)
        else:
            self._redis.delete(lock_key)

    def update_last_room(self, uuid: Optional[str], room_id: str) -> None:
        """Update the last room used by a speaker.

        Parameters
        ----------
        uuid:
            Speaker UUID (or None to skip update).
        room_id:
            Room identifier.
        """
        if not uuid:
            return
        now = time.time()
        self._redis.set(self._key_last_room(uuid), room_id, ex=3600)
        self._redis.set(self._key_last_seen(uuid), str(now), ex=3600)

        # Publish active room event
        self._event_bus.publish(
            "voice/active_room",
            {
                "uuid": uuid,
                "room_id": room_id,
            },
        )


__all__ = ["ConversationRouter"]

