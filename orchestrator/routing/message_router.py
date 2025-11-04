"""Intent classification utilities for the HALCYON orchestrator."""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Dict, Mapping, Optional

from pydantic import BaseModel, Field

from orchestrator.policy_engine.trust_scoring import Role
from orchestrator.routing.intent_map import detect_intent


@dataclass
class IntentClassification:
    """Outcome of lightweight keyword intent parsing."""

    intent: Optional[str]
    slots: Dict[str, object]
    sensitive: bool
    persona_bias: str
    confidence: float


class RouterConfig(BaseModel):
    """Configuration describing keyword -> entity mappings."""

    light_entities: Dict[str, str] = Field(
        default_factory=lambda: {
            "kitchen": "light.kitchen",
            "living room": "light.living_room",
            "hall": "light.hallway",
        }
    )
    lock_entities: Dict[str, str] = Field(
        default_factory=lambda: {
            "front": "lock.front_door",
            "back": "lock.back_door",
            "garage": "lock.garage_entry",
        }
    )
    climate_entities: Dict[str, str] = Field(
        default_factory=lambda: {
            "living": "climate.living",
            "bedroom": "climate.bedroom",
        }
    )
    media_entities: Dict[str, str] = Field(
        default_factory=lambda: {
            "living": "media_player.living_room",
            "kitchen": "media_player.kitchen",
        }
    )
    garage_entity: str = "cover.garage"
    default_light: Optional[str] = "light.living_room"
    default_lock: Optional[str] = "lock.front_door"
    default_media_player: Optional[str] = "media_player.living_room"
    default_climate: Optional[str] = "climate.living"


class MessageRouter:
    """Applies deterministic keyword heuristics to classify intents."""

    def __init__(self, config: Optional[RouterConfig] = None) -> None:
        self.config = config or RouterConfig()

    # ------------------------------------------------------------------
    def classify(self, text: str, role: Role) -> IntentClassification:
        """Return the canonical intent, slots, and persona bias for ``text``."""

        lowered = text.lower().strip()
        if not lowered:
            return IntentClassification(intent=None, slots={}, sensitive=False, persona_bias="HALSTON", confidence=0.0)

        media_intent, media_slots = detect_intent(lowered)
        if media_intent:
            return IntentClassification(
                intent=media_intent,
                slots=media_slots,
                sensitive=False,
                persona_bias="HALSTON",
                confidence=0.85,
            )

        # Security-first commands -------------------------------------------------
        if "disarm" in lowered and "alarm" in lowered:
            return IntentClassification(
                intent="disarm_alarm",
                slots={},
                sensitive=True,
                persona_bias="SCARLET",
                confidence=0.9,
            )
        if "unlock" in lowered and "door" in lowered:
            slots = {"entity_id": self._match_entity(lowered, self.config.lock_entities, self.config.default_lock)}
            return IntentClassification(
                intent="unlock_door",
                slots=slots,
                sensitive=True,
                persona_bias="SCARLET",
                confidence=0.85,
            )
        if "open" in lowered and "garage" in lowered:
            slots = {"entity_id": self.config.garage_entity}
            return IntentClassification(
                intent="open_garage",
                slots=slots,
                sensitive=True,
                persona_bias="SCARLET",
                confidence=0.8,
            )
        if "lock" in lowered and "door" in lowered:
            slots = {"entity_id": self._match_entity(lowered, self.config.lock_entities, self.config.default_lock)}
            return IntentClassification(
                intent="lock_door",
                slots=slots,
                sensitive=True,
                persona_bias="SCARLET" if role in {"guest", "unknown"} else "neutral",
                confidence=0.8,
            )

        # Lighting ----------------------------------------------------------------
        if any(token in lowered for token in ("turn on", "switch on", "lights on")):
            slots = {"entity_id": self._match_entity(lowered, self.config.light_entities, self.config.default_light)}
            return IntentClassification(
                intent="turn_on_light",
                slots=slots,
                sensitive=False,
                persona_bias="HALSTON",
                confidence=0.75,
            )
        if any(token in lowered for token in ("turn off", "switch off", "lights off")):
            slots = {"entity_id": self._match_entity(lowered, self.config.light_entities, self.config.default_light)}
            return IntentClassification(
                intent="turn_off_light",
                slots=slots,
                sensitive=False,
                persona_bias="HALSTON",
                confidence=0.75,
            )

        # Climate -----------------------------------------------------------------
        if "temperature" in lowered or "thermostat" in lowered:
            slots = {
                "entity_id": self._match_entity(lowered, self.config.climate_entities, self.config.default_climate),
                "temperature": self._extract_temperature(lowered),
            }
            return IntentClassification(
                intent="set_temperature",
                slots=slots,
                sensitive=False,
                persona_bias="HALSTON",
                confidence=0.7,
            )

        # Media -------------------------------------------------------------------
        if "play" in lowered or "pause" in lowered:
            slots = {"entity_id": self._match_entity(lowered, self.config.media_entities, self.config.default_media_player)}
            return IntentClassification(
                intent="media_play_pause",
                slots=slots,
                sensitive=False,
                persona_bias="HALSTON",
                confidence=0.6,
            )

        # No clear intent ---------------------------------------------------------
        bias = "SCARLET" if role in {"guest", "unknown"} else "HALSTON"
        return IntentClassification(intent=None, slots={}, sensitive=False, persona_bias=bias, confidence=0.3)

    # ------------------------------------------------------------------
    def _match_entity(self, lowered_text: str, vocabulary: Mapping[str, str], default: Optional[str]) -> Optional[str]:
        for keyword, entity in sorted(vocabulary.items(), key=lambda item: len(item[0]), reverse=True):
            if keyword in lowered_text:
                return entity
        return default

    def _extract_temperature(self, lowered_text: str) -> Optional[float]:
        match = re.search(r"(-?\d{2,3})(?:\.?\d)?", lowered_text)
        if match:
            try:
                return float(match.group(1))
            except ValueError:  # pragma: no cover - defensive guard
                return None
        return None
