"""Input multiplexer for routing audio frames from multiple mics to STT."""
from __future__ import annotations

import logging
import threading
import time
from typing import Callable, Dict, Optional

from orchestrator.logging.event_bus import EventBus
from services.voice_pipeline.room_registry import RoomRegistry
from services.voice_pipeline.stt_engine import FRAME_SIZE_BYTES, STTEngine
from services.voice_pipeline.wakeword_bus import WakeEvent, WakewordBus

_LOGGER = logging.getLogger(__name__)


class InputMux:
    """Multiplexes audio input from multiple microphones to STT engine."""

    def __init__(
        self,
        stt_engine: STTEngine,
        wakeword_bus: WakewordBus,
        room_registry: RoomRegistry,
        *,
        event_bus: Optional[EventBus] = None,
        wakeword_listener: Optional[Callable[[bytes], None]] = None,
    ) -> None:
        """Initialize the input multiplexer.

        Parameters
        ----------
        stt_engine:
            STTEngine instance for speech recognition.
        wakeword_bus:
            WakewordBus instance for wakeword event handling.
        room_registry:
            RoomRegistry instance for mic-to-room mapping.
        event_bus:
            EventBus instance for MQTT diagnostics. If None, creates a default one.
        wakeword_listener:
            Optional callback for wakeword detection (external integration point).
            If provided, frames are passed to this callback before STT.
        """
        self._stt = stt_engine
        self._wakeword_bus = wakeword_bus
        self._room_registry = room_registry
        self._event_bus = event_bus or EventBus()
        self._wakeword_listener = wakeword_listener

        # Track active sessions: mic_id -> (uuid, temp_id, start_time)
        self._active_sessions: Dict[str, tuple[Optional[str], str, float]] = {}
        self._lock = threading.RLock()

        # Subscribe to wakeword events
        self._wakeword_bus.subscribe(self._on_wake_event)

    def _on_wake_event(self, event: WakeEvent) -> None:
        """Handle wakeword detection event."""
        mic_id = event.mic_id
        room_id = self._room_registry.get_room_for_mic(mic_id)
        if not room_id:
            _LOGGER.warning("Wake event from unknown mic: %s", mic_id)
            return

        # Generate temp session ID
        temp_id = f"mic:{mic_id}:{int(time.time())}"

        with self._lock:
            # If another mic is active for the same uuid, release it
            # (This prevents crosstalk - only one mic per conversation)
            active_uuid = None
            for other_mic, (uuid, _, _) in list(self._active_sessions.items()):
                if uuid:
                    active_uuid = uuid
                    break

            # Activate this mic
            self._active_sessions[mic_id] = (None, temp_id, time.time())

            # Update stream state
            self._event_bus.publish(
                "voice/stream_state",
                {
                    "mic_id": mic_id,
                    "state": "awake",
                    "uuid": None,
                    "temp_id": temp_id,
                },
            )

        _LOGGER.debug("Wake event from mic %s (room %s), temp_id: %s", mic_id, room_id, temp_id)

    def push(self, mic_id: str, frame_20ms: bytes) -> None:
        """Push a 20ms audio frame from a microphone.

        Parameters
        ----------
        mic_id:
            Microphone identifier.
        frame_20ms:
            20ms PCM audio frame (640 bytes at 16kHz, 16-bit mono).
        """
        if len(frame_20ms) != FRAME_SIZE_BYTES:
            _LOGGER.debug("Dropping malformed frame from mic %s (size: %d)", mic_id, len(frame_20ms))
            return

        with self._lock:
            # Check if this mic has an active session
            session = self._active_sessions.get(mic_id)
            if not session:
                # No active session - pass to wakeword listener only
                if self._wakeword_listener:
                    try:
                        self._wakeword_listener(frame_20ms)
                    except Exception:
                        _LOGGER.exception("Wakeword listener error")
                return

            uuid, temp_id, start_time = session

        # Active session exists - route to STT
        try:
            self._stt.push_audio(frame_20ms)
        except Exception:
            _LOGGER.exception("STT push error for mic %s", mic_id)

        # Update stream state (throttled to avoid spam)
        now = time.time()
        if now - start_time < 0.1 or int(now * 10) % 10 == 0:  # Update every 100ms or so
            self._event_bus.publish(
                "voice/stream_state",
                {
                    "mic_id": mic_id,
                    "state": "stt",
                    "uuid": uuid,
                    "temp_id": temp_id,
                },
            )

    def release_session(self, mic_id: str) -> None:
        """Release an active microphone session (end of utterance).

        Parameters
        ----------
        mic_id:
            Microphone identifier.
        """
        with self._lock:
            if mic_id in self._active_sessions:
                del self._active_sessions[mic_id]
                self._event_bus.publish(
                    "voice/stream_state",
                    {
                        "mic_id": mic_id,
                        "state": "idle",
                    },
                )
                _LOGGER.debug("Released session for mic %s", mic_id)

    def set_uuid_for_session(self, mic_id: str, uuid: Optional[str]) -> None:
        """Update the UUID for an active session (after identity resolution).

        Parameters
        ----------
        mic_id:
            Microphone identifier.
        uuid:
            Stable speaker UUID (or None).
        """
        with self._lock:
            if mic_id in self._active_sessions:
                _, temp_id, start_time = self._active_sessions[mic_id]
                self._active_sessions[mic_id] = (uuid, temp_id, start_time)
                _LOGGER.debug("Updated UUID for mic %s: %s", mic_id, uuid)

    def get_active_mic_for_uuid(self, uuid: Optional[str]) -> Optional[str]:
        """Get the active microphone for a given UUID.

        Parameters
        ----------
        uuid:
            Speaker UUID.

        Returns
        -------
        Microphone ID if found, None otherwise.
        """
        with self._lock:
            for mic_id, (session_uuid, _, _) in self._active_sessions.items():
                if session_uuid == uuid:
                    return mic_id
            return None

    def get_temp_id_for_mic(self, mic_id: str) -> Optional[str]:
        """Get the temporary session ID for a microphone.

        Parameters
        ----------
        mic_id:
            Microphone identifier.

        Returns
        -------
        Temporary session ID if mic is active, None otherwise.
        """
        with self._lock:
            session = self._active_sessions.get(mic_id)
            if session:
                return session[1]  # temp_id
            return None


__all__ = ["InputMux"]

