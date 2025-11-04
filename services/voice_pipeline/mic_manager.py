"""Microphone health tracking and management for multi-room voice pipeline."""
from __future__ import annotations

import os
import threading
import time
from dataclasses import dataclass
from typing import Callable, Dict, Optional

from orchestrator.logging.event_bus import EventBus


@dataclass
class MicStatus:
    """Status information for a microphone."""

    mic_id: str
    room_id: str
    device: str
    last_heartbeat: float
    rms_level: float
    vad_active: bool
    alive: bool


class MicManager:
    """Manages microphone registration and health tracking."""

    def __init__(
        self,
        *,
        event_bus: Optional[EventBus] = None,
        heartbeat_timeout_sec: Optional[float] = None,
    ) -> None:
        """Initialize the microphone manager.

        Parameters
        ----------
        event_bus:
            EventBus instance for MQTT diagnostics. If None, creates a default one.
        heartbeat_timeout_sec:
            Timeout in seconds for considering a mic dead. If None, reads from
            MIC_HEARTBEAT_TIMEOUT_SEC environment variable (default 8.0).
        """
        self._event_bus = event_bus or EventBus()
        timeout_env = heartbeat_timeout_sec
        if timeout_env is None:
            timeout_env = float(os.getenv("MIC_HEARTBEAT_TIMEOUT_SEC", "8.0"))
        self._heartbeat_timeout = timeout_env

        self._mics: Dict[str, MicStatus] = {}
        self._lock = threading.RLock()

    def register_mic(self, mic_id: str, room_id: str, device: str, caps: Optional[Dict] = None) -> None:
        """Register a microphone with the manager.

        Parameters
        ----------
        mic_id:
            Unique microphone identifier.
        room_id:
            Room where the microphone is located.
        room_id:
            Audio device identifier (e.g., "hw:2,0").
        caps:
            Optional capabilities dictionary (reserved for future use).
        """
        with self._lock:
            now = time.time()
            self._mics[mic_id] = MicStatus(
                mic_id=mic_id,
                room_id=room_id,
                device=device,
                last_heartbeat=now,
                rms_level=0.0,
                vad_active=False,
                alive=True,
            )

    def heartbeat(self, mic_id: str, rms_level: float, vad: bool) -> None:
        """Update microphone heartbeat with current status.

        Parameters
        ----------
        mic_id:
            Microphone identifier.
        rms_level:
            RMS audio level (0.0 to 1.0).
        vad:
            Voice activity detection state.
        """
        with self._lock:
            if mic_id not in self._mics:
                return
            status = self._mics[mic_id]
            now = time.time()
            status.last_heartbeat = now
            status.rms_level = max(0.0, min(1.0, rms_level))
            status.vad_active = vad
            status.alive = True

            # Publish heartbeat to MQTT
            self._event_bus.publish(
                "voice/mic/heartbeat",
                {
                    "mic_id": mic_id,
                    "room_id": status.room_id,
                    "rms": round(status.rms_level, 3),
                    "vad": vad,
                    "alive": True,
                },
            )

    def is_alive(self, mic_id: str) -> bool:
        """Check if a microphone is alive (heartbeat within timeout).

        Returns
        -------
        True if mic is registered and heartbeat is recent, False otherwise.
        """
        with self._lock:
            if mic_id not in self._mics:
                return False
            status = self._mics[mic_id]
            now = time.time()
            age = now - status.last_heartbeat
            alive = age <= self._heartbeat_timeout
            if status.alive != alive:
                status.alive = alive
                # Publish state change
                self._event_bus.publish(
                    "voice/mic/heartbeat",
                    {
                        "mic_id": mic_id,
                        "room_id": status.room_id,
                        "rms": round(status.rms_level, 3),
                        "vad": status.vad_active,
                        "alive": alive,
                    },
                )
            return alive

    def best_mic_for_room(self, room_id: str) -> Optional[str]:
        """Get the best (alive) microphone for a room.

        Parameters
        ----------
        room_id:
            Room identifier.

        Returns
        -------
        Microphone ID if found, None otherwise.
        """
        with self._lock:
            candidates = []
            for mic_id, status in self._mics.items():
                if status.room_id == room_id and self.is_alive(mic_id):
                    candidates.append((mic_id, status.rms_level))
            if not candidates:
                return None
            # Return mic with highest RMS (assuming it's closest/most active)
            candidates.sort(key=lambda x: x[1], reverse=True)
            return candidates[0][0]

    def capture_loop(
        self,
        mic_id: str,
        frame_callback: Callable[[bytes], None],
        *,
        sample_rate: int = 16000,
        frame_duration_ms: int = 20,
    ) -> None:
        """Placeholder entry point for microphone capture loop.

        This is a placeholder that should be replaced by actual audio capture
        implementation. It would pipe 20ms frames to the wakeword/STT pipeline.

        Parameters
        ----------
        mic_id:
            Microphone identifier.
        frame_callback:
            Callback function receiving 20ms PCM frames (bytes).
        sample_rate:
            Audio sample rate (default 16000 Hz).
        frame_duration_ms:
            Frame duration in milliseconds (default 20ms).
        """
        # Placeholder: actual implementation would:
        # 1. Open audio device from self._mics[mic_id].device
        # 2. Read audio in 20ms chunks
        # 3. Call frame_callback(frame_bytes) for each chunk
        # 4. Update heartbeat with RMS level and VAD state
        pass

    def get_status(self, mic_id: str) -> Optional[MicStatus]:
        """Get current status for a microphone.

        Returns
        -------
        MicStatus if mic is registered, None otherwise.
        """
        with self._lock:
            return self._mics.get(mic_id)

    def list_mics(self) -> list[str]:
        """List all registered microphone IDs."""
        with self._lock:
            return list(self._mics.keys())


__all__ = ["MicManager", "MicStatus"]

