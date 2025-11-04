"""HALSTON persona runtime implementation."""
from __future__ import annotations

from collections import deque
from typing import Deque, Dict, Iterable, Optional

from pydantic import BaseModel, Field

from orchestrator.policy_engine.access_control import AccessDecision


class IntentLexicon(BaseModel):
    """Configurable keyword to intent mapping."""

    intent: str
    keywords: Iterable[str]


class HalstonConfig(BaseModel):
    """Configuration values for the HALSTON agent."""

    name: str = "Halston"
    tone: str = "calm, reassuring"
    intent_lexicon: Iterable[IntentLexicon] = Field(default_factory=list)
    fallback_intent: str = "general.assistance"
    max_history: int = Field(default=6, ge=1)


class ConversationMemory(BaseModel):
    """Simple short-term memory buffer for conversational turns."""

    user_text: str
    intent: str


class HalstonAgent:
    """Conversational agent for the HALSTON persona."""

    def __init__(self, config: Optional[HalstonConfig] = None) -> None:
        self.config = config or HalstonConfig()
        self._history: Deque[ConversationMemory] = deque(maxlen=self.config.max_history)
        self._lexicon = list(self.config.intent_lexicon)

    def infer_intent(self, text: str, hint: Optional[str] = None) -> str:
        """Infer the most likely intent using lexicon matching."""

        if hint:
            return hint

        lowered = text.lower()
        for lex in self._lexicon:
            if any(keyword.lower() in lowered for keyword in lex.keywords):
                return lex.intent
        return self.config.fallback_intent

    def generate_response(self, text: str, *, intent: Optional[str], metadata: Dict[str, object]) -> str:
        """Generate a response string informed by prior history."""

        intent_name = intent or self.config.fallback_intent
        self._history.append(ConversationMemory(user_text=text, intent=intent_name))
        context_summary = self._summarize_context()
        polite_prefix = "Certainly." if intent_name != self.config.fallback_intent else "Of course."
        response = (
            f"{polite_prefix} {self.config.name} here."
            f" I will handle the '{intent_name}' request with {self.config.tone} attention."
        )
        if context_summary:
            response += f" We have recently discussed {context_summary}."
        return response

    def build_denied_response(self, decision: AccessDecision) -> str:
        """Return a trust-aware denial message."""

        if decision.reason:
            reason = decision.reason
        elif decision.required_trust is not None:
            reason = (
                f"This action requires {decision.required_trust.name.title()} clearance"
                " and cannot be performed just now."
            )
        else:
            reason = "I am unable to comply with that request."
        return (
            f"Apologies, but I must decline. {reason}"
            " Please consult an administrator if you believe this is in error."
        )

    # Internal helpers -------------------------------------------------

    def _summarize_context(self) -> str:
        if not self._history:
            return ""
        intents = {entry.intent for entry in self._history}
        if len(intents) == 1:
            intent = next(iter(intents))
            return f"a series of '{intent}' tasks"
        return "a mixture of tasks"
