"""Wakeword event bus with collision resolution for multi-room voice pipeline."""
from __future__ import annotations

import threading
import time
from dataclasses import dataclass
from typing import Callable, Dict, List, Optional

import redis


@dataclass
class WakeEvent:
    """Wakeword detection event."""

    mic_id: str
    confidence: float
    keyword: str
    timestamp: float


class WakewordBus:
    """Event bus for wakeword events with collision resolution."""

    def __init__(
        self,
        *,
        redis_url: str = "redis://localhost:6379/0",
        collision_window_ms: float = 300.0,
    ) -> None:
        """Initialize the wakeword bus.

        Parameters
        ----------
        redis_url:
            Redis connection URL for storing last room state.
        collision_window_ms:
            Time window in milliseconds for collision detection (default 300ms).
        """
        self._redis = redis.from_url(redis_url, decode_responses=True)
        self._collision_window = collision_window_ms / 1000.0
        self._subscribers: List[Callable[[WakeEvent], None]] = []
        self._lock = threading.RLock()

        # Track recent wake events for collision resolution
        self._recent_events: List[WakeEvent] = []
        self._last_emit_time: Dict[str, float] = {}  # Per-mic debouncing

    def subscribe(self, handler: Callable[[WakeEvent], None]) -> None:
        """Subscribe to wakeword events.

        Parameters
        ----------
        handler:
            Callback function that receives WakeEvent objects.
        """
        with self._lock:
            if handler not in self._subscribers:
                self._subscribers.append(handler)

    def unsubscribe(self, handler: Callable[[WakeEvent], None]) -> None:
        """Unsubscribe from wakeword events."""
        with self._lock:
            if handler in self._subscribers:
                self._subscribers.remove(handler)

    def emit_wake(self, mic_id: str, confidence: float, keyword: str = "halcyon") -> None:
        """Emit a wakeword detection event.

        Parameters
        ----------
        mic_id:
            Microphone identifier that detected the wakeword.
        confidence:
            Detection confidence (0.0 to 1.0).
        keyword:
            Wakeword keyword that was detected (default "halcyon").
        """
        now = time.time()

        # Debounce per-mic: ignore if too soon after last emit
        last_emit = self._last_emit_time.get(mic_id, 0.0)
        if now - last_emit < 0.5:  # 500ms debounce per mic
            return

        event = WakeEvent(mic_id=mic_id, confidence=confidence, keyword=keyword, timestamp=now)

        with self._lock:
            # Add to recent events
            self._recent_events.append(event)
            self._last_emit_time[mic_id] = now

            # Clean old events (keep only within collision window)
            cutoff = now - self._collision_window
            self._recent_events = [e for e in self._recent_events if e.timestamp > cutoff]

            # Check for collisions (multiple mics within window)
            recent_in_window = [e for e in self._recent_events if now - e.timestamp <= self._collision_window]

            if len(recent_in_window) > 1:
                # Collision detected - resolve
                winner = self._resolve_collision(recent_in_window)
                if winner:
                    # Only emit the winner
                    self._notify_subscribers(winner)
            else:
                # No collision, emit immediately
                self._notify_subscribers(event)

    def _resolve_collision(self, events: List[WakeEvent]) -> Optional[WakeEvent]:
        """Resolve wakeword collision by selecting the best event.

        Resolution strategy:
        1. Higher confidence wins
        2. If tie, prefer room of last interaction (from Redis)

        Parameters
        ----------
        events:
            List of wake events within collision window.

        Returns
        -------
        Winning WakeEvent, or None if resolution fails.
        """
        if not events:
            return None

        # Sort by confidence (descending)
        events_sorted = sorted(events, key=lambda e: e.confidence, reverse=True)

        # If highest confidence is clearly better, use it
        if len(events_sorted) == 1:
            return events_sorted[0]

        top_conf = events_sorted[0].confidence
        second_conf = events_sorted[1].confidence if len(events_sorted) > 1 else 0.0

        # If there's a clear winner (confidence difference > 0.1), use it
        if top_conf - second_conf > 0.1:
            return events_sorted[0]

        # Tie or close - use last room heuristic
        # Extract mic IDs and try to get room from Redis
        # (This assumes mic_id format like "mic_lounge_1" or we need room lookup)
        # For now, prefer the first event (will be refined with room lookup)
        return events_sorted[0]

    def _notify_subscribers(self, event: WakeEvent) -> None:
        """Notify all subscribers of a wake event."""
        # Copy subscribers list to avoid lock issues during callback
        with self._lock:
            subscribers = list(self._subscribers)

        for handler in subscribers:
            try:
                handler(event)
            except Exception:
                # Subscriber errors should not break the bus
                pass

    def get_recent_events(self, window_sec: float = 1.0) -> List[WakeEvent]:
        """Get recent wake events within a time window.

        Parameters
        ----------
        window_sec:
            Time window in seconds.

        Returns
        -------
        List of recent WakeEvent objects.
        """
        now = time.time()
        cutoff = now - window_sec
        with self._lock:
            return [e for e in self._recent_events if e.timestamp > cutoff]


__all__ = ["WakewordBus", "WakeEvent"]

