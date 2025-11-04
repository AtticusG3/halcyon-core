"""SCARLET persona escalation protocols."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Callable, Dict, Iterable, Optional

from pydantic import BaseModel, Field

from orchestrator.policy_engine.access_control import AccessDecision


class EscalationHook(BaseModel):
    """Defines an escalation callback and the intents that should trigger it."""

    intents: Iterable[str]
    callback: Callable[[str, Dict[str, object]], None]

    class Config:
        arbitrary_types_allowed = True


class ScarletConfig(BaseModel):
    """Configuration for SCARLET persona behaviour."""

    name: str = "Scarlet"
    tone: str = "quiet, direct"
    monitored_intents: Iterable[str] = Field(
        default_factory=lambda: ("security.alert", "system.override")
    )
    fallback_intent: str = "security.review"
    escalation_hooks: Iterable[EscalationHook] = Field(default_factory=list)


@dataclass
class IncidentRecord:
    """Captured audit record of a security incident."""

    intent: str
    transcript: str
    metadata: Dict[str, object]


class ScarletAgent:
    """Security persona responsible for high-risk interactions."""

    def __init__(self, config: Optional[ScarletConfig] = None) -> None:
        self.config = config or ScarletConfig()
        self._incidents: list[IncidentRecord] = []
        self._monitored = {intent for intent in self.config.monitored_intents}
        self._hooks = list(self.config.escalation_hooks)

    def infer_intent(self, text: str, hint: Optional[str] = None) -> str:
        """Prioritize security-related intents."""

        if hint:
            return hint

        lowered = text.lower()
        if any(keyword in lowered for keyword in ("panic", "intruder", "help")):
            return "security.alert"
        if "override" in lowered or "admin" in lowered:
            return "system.override"
        return self.config.fallback_intent

    def generate_response(self, text: str, *, intent: Optional[str], metadata: Dict[str, object]) -> str:
        """Produce a concise response and trigger escalation hooks if needed."""

        intent_name = intent or self.config.fallback_intent
        record = IncidentRecord(intent=intent_name, transcript=text, metadata=metadata)
        self._incidents.append(record)

        if intent_name in self._monitored:
            self._notify_hooks(intent_name, metadata)

        acknowledgement = "Understood." if intent_name != "security.alert" else "Alert acknowledged."
        response = (
            f"{acknowledgement} {self.config.name} assuming control."
            f" Intent '{intent_name}' is being handled with {self.config.tone} authority."
        )
        if intent_name in self._monitored:
            response += " I am escalating to the appropriate safeguards."
        return response

    def build_denied_response(self, decision: AccessDecision) -> str:
        """Return a terse denial respecting security posture."""

        reason = decision.reason or "The requested action is outside permitted scope."
        return f"Denied. {reason}"

    def recent_incidents(self, limit: int = 10) -> list[IncidentRecord]:
        """Return the most recent incident records."""

        return self._incidents[-limit:]

    # Internal ---------------------------------------------------------

    def _notify_hooks(self, intent: str, metadata: Dict[str, object]) -> None:
        for hook in self._hooks:
            if intent in hook.intents:
                hook.callback(intent, metadata)
