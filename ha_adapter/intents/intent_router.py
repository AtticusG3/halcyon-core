"""Intent routing to Home Assistant via MQTT."""
from __future__ import annotations

from typing import Any, Dict, Optional, TYPE_CHECKING

from pydantic import BaseModel, Field

from services.event_bridge.homeassistant_mqtt import HAMQTTBridge

if TYPE_CHECKING:  # pragma: no cover - typing only
    from ha_adapter.intents.intent_media import MediaIntentHandler


class IntentContext(BaseModel):
    """Runtime context describing the caller's trust posture."""

    role: str = Field(..., description="Derived role: owner/household/guest/unknown")
    allow_sensitive: bool = Field(
        default=False,
        description="Whether security-sensitive service calls are permitted.",
    )
    mode: str = Field(
        default="home",
        description="Environmental mode (home/away/night/maintenance/incident).",
    )
    speaker_uuid: Optional[str] = Field(default=None, description="Stable speaker UUID if known.")
    session_id: Optional[str] = Field(default=None, description="Temporary session identifier.")
    persona: str = Field(default="HALSTON", description="Active persona label.")


class IntentResult(BaseModel):
    """Result returned after attempting to fulfill an intent."""

    ok: bool
    spoken: str
    details: Dict[str, Any] = Field(default_factory=dict)


class IntentRouter:
    """Maps normalized intents to Home Assistant service calls."""

    def __init__(self, mqtt_bridge: HAMQTTBridge, media_handler: "MediaIntentHandler" | None = None) -> None:
        self._mqtt = mqtt_bridge
        self._media = media_handler

    # ------------------------------------------------------------------
    # Public API
    def handle(self, intent: str, slots: Dict[str, Any], ctx: IntentContext) -> IntentResult:
        """Dispatch the requested intent."""

        normalized = intent.strip().lower()
        if not normalized:
            return self._deny("I didn't catch that.")

        if normalized in {"unlock_door", "open_garage", "disarm_alarm"}:
            if not ctx.allow_sensitive:
                return self._deny("That function is not available right now.")

        if normalized in {"media_recommend", "media_request", "media_add_to_list"}:
            if self._media is None:
                return self._deny("Media services are not configured.")
            if normalized == "media_recommend":
                return self._media.handle_recommend(ctx, slots)
            if normalized == "media_request":
                return self._media.handle_add_request(ctx, slots)
            return self._media.handle_add_to_list(ctx, slots)

        handler = getattr(self, f"_intent_{normalized}", None)
        if handler is None:
            return self._deny("I can’t do that yet.")
        return handler(slots, ctx)

    # ------------------------------------------------------------------
    # Intent handlers
    def _intent_turn_on_light(self, slots: Dict[str, Any], ctx: IntentContext) -> IntentResult:
        entity = slots.get("entity_id")
        if not entity:
            return self._deny("Which light?")
        ok = self._mqtt.call_service("light", "turn_on", {"entity_id": entity})
        return self._result(ok, "Done.", failure="I couldn't reach that light.")

    def _intent_turn_off_light(self, slots: Dict[str, Any], ctx: IntentContext) -> IntentResult:
        entity = slots.get("entity_id")
        if not entity:
            return self._deny("Which light?")
        ok = self._mqtt.call_service("light", "turn_off", {"entity_id": entity})
        return self._result(ok, "Done.", failure="I couldn't reach that light.")

    def _intent_set_temperature(self, slots: Dict[str, Any], ctx: IntentContext) -> IntentResult:
        entity = slots.get("entity_id", "climate.living")
        if "temperature" not in slots:
            return self._deny("What temperature?")
        ok = self._mqtt.call_service(
            "climate",
            "set_temperature",
            {"entity_id": entity, "temperature": slots["temperature"]},
        )
        return self._result(ok, "Temperature set.")

    def _intent_media_play_pause(self, slots: Dict[str, Any], ctx: IntentContext) -> IntentResult:
        entity = slots.get("entity_id", "media_player.living_room")
        ok = self._mqtt.call_service(
            "media_player",
            "media_play_pause",
            {"entity_id": entity},
        )
        return self._result(ok, "Okay.", failure="I couldn't control that player.")

    def _intent_lock_door(self, slots: Dict[str, Any], ctx: IntentContext) -> IntentResult:
        entity = slots.get("entity_id", "lock.front_door")
        ok = self._mqtt.call_service("lock", "lock", {"entity_id": entity})
        return self._result(ok, "Locked.", failure="I couldn't lock it.")

    def _intent_unlock_door(self, slots: Dict[str, Any], ctx: IntentContext) -> IntentResult:
        entity = slots.get("entity_id", "lock.front_door")
        ok = self._mqtt.call_service("lock", "unlock", {"entity_id": entity})
        return self._result(ok, "Unlocked.", failure="I couldn't unlock it.")

    def _intent_open_garage(self, slots: Dict[str, Any], ctx: IntentContext) -> IntentResult:
        entity = slots.get("entity_id", "cover.garage")
        ok = self._mqtt.call_service("cover", "open_cover", {"entity_id": entity})
        return self._result(ok, "Opening the garage.", failure="I couldn't open it.")

    def _intent_disarm_alarm(self, slots: Dict[str, Any], ctx: IntentContext) -> IntentResult:
        code = slots.get("code")
        if not code:
            return self._deny("I need the code to disarm.")
        entity = slots.get("entity_id", "alarm_control_panel.home")
        ok = self._mqtt.call_service(
            "alarm_control_panel",
            "alarm_disarm",
            {"entity_id": entity, "code": code},
        )
        return self._result(ok, "Alarm disarmed.", failure="I couldn't disarm the alarm.")

    # ------------------------------------------------------------------
    # Helpers
    def _result(self, ok: bool, success: str, *, failure: str | None = None) -> IntentResult:
        spoken = success if ok else (failure or "I couldn't complete that.")
        return IntentResult(ok=ok, spoken=spoken)

    def _deny(self, reason: str) -> IntentResult:
        return IntentResult(ok=False, spoken=reason)


__all__ = ["IntentRouter", "IntentContext", "IntentResult"]
