"""Output routing for multi-room TTS audio delivery."""
from __future__ import annotations

import logging
from typing import Dict, Optional

from orchestrator.logging.event_bus import EventBus
from services.voice_pipeline.conversation_router import ConversationRouter
from services.voice_pipeline.room_registry import RoomRegistry
from services.voice_pipeline.wyoming_client import WyomingClient

_LOGGER = logging.getLogger(__name__)


class OutputRouter:
    """Routes TTS audio output to appropriate rooms with privacy/DND handling."""

    def __init__(
        self,
        room_registry: RoomRegistry,
        conversation_router: ConversationRouter,
        *,
        event_bus: Optional[EventBus] = None,
    ) -> None:
        """Initialize the output router.

        Parameters
        ----------
        room_registry:
            RoomRegistry instance for room configuration.
        conversation_router:
            ConversationRouter instance for room selection and privacy checks.
        event_bus:
            EventBus instance for MQTT diagnostics. If None, creates a default one.
        """
        self._room_registry = room_registry
        self._conversation_router = conversation_router
        self._event_bus = event_bus or EventBus()

        # Cache Wyoming clients per room (host:port)
        self._wyoming_clients: Dict[tuple[str, int], WyomingClient] = {}

    def _get_wyoming_client(self, host: str, port: int) -> WyomingClient:
        """Get or create a Wyoming client for a room."""
        key = (host, port)
        if key not in self._wyoming_clients:
            self._wyoming_clients[key] = WyomingClient(host, port)
        return self._wyoming_clients[key]

    def route(
        self,
        persona: str,
        uuid: Optional[str],
        room_id: str,
        wav_bytes: bytes,
    ) -> bool:
        """Route TTS audio to a room.

        Parameters
        ----------
        persona:
            Persona name ("HALSTON" or "SCARLET").
        uuid:
            Speaker UUID (for privacy/DND checks).
        room_id:
            Target room identifier.
        wav_bytes:
            WAV audio bytes to route.

        Returns
        -------
        True if routing succeeded, False otherwise.
        """
        # Check if speech is allowed in this room
        if not self._conversation_router.can_speak_in(room_id, persona):
            # Privacy zone or DND: send chime only (or MQTT notification)
            if self._room_registry.is_privacy_zone(room_id):
                _LOGGER.debug("Privacy zone %s: denying speech output", room_id)
                # Send short chime or notification
                chime = WyomingClient.create_chime_wav(duration_ms=200)
                try:
                    host, port = self._room_registry.get_output_target(room_id)
                    client = self._get_wyoming_client(host, port)
                    client.send_tts_sync(chime)
                except Exception as exc:
                    _LOGGER.warning("Failed to send privacy chime: %s", exc)

                # Publish MQTT notification
                self._event_bus.publish(
                    "voice/error",
                    {
                        "code": "privacy_zone",
                        "message": f"Speech denied in privacy zone: {room_id}",
                        "room_id": room_id,
                        "uuid": uuid,
                    },
                )
                return False

            if self._room_registry.is_dnd_zone(room_id):
                # DND: allow SCARLET critical only
                if persona != "SCARLET":
                    _LOGGER.debug("DND zone %s: denying speech for %s", room_id, persona)
                    chime = WyomingClient.create_chime_wav(duration_ms=150)
                    try:
                        host, port = self._room_registry.get_output_target(room_id)
                        client = self._get_wyoming_client(host, port)
                        client.send_tts_sync(chime)
                    except Exception as exc:
                        _LOGGER.warning("Failed to send DND chime: %s", exc)
                    return False
                # SCARLET can override DND
                _LOGGER.debug("DND zone %s: allowing SCARLET critical announcement", room_id)

        # Get Wyoming target for room
        try:
            host, port = self._room_registry.get_output_target(room_id)
        except Exception as exc:
            _LOGGER.error("Failed to get output target for room %s: %s", room_id, exc)
            self._event_bus.publish(
                "voice/error",
                {
                    "code": "room_not_found",
                    "message": f"Room {room_id} not found",
                    "room_id": room_id,
                },
            )
            return False

        # Send audio to Wyoming
        try:
            client = self._get_wyoming_client(host, port)
            success = client.send_tts_sync(wav_bytes)
            if success:
                _LOGGER.debug("Routed TTS to room %s (%s:%d)", room_id, host, port)
                return True
            else:
                _LOGGER.warning("Wyoming TTS send returned False for room %s", room_id)
                return False
        except Exception as exc:
            _LOGGER.exception("Failed to route TTS to room %s: %s", room_id, exc)
            self._event_bus.publish(
                "voice/error",
                {
                    "code": "routing_failed",
                    "message": f"Failed to route TTS: {exc}",
                    "room_id": room_id,
                    "uuid": uuid,
                },
            )
            return False


__all__ = ["OutputRouter"]

