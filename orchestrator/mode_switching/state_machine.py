"""Persona mode switching finite state machine.

This module implements the logic that determines when the runtime should
switch between the HALSTON and SCARLET personas. The rules are based on a
combination of threat assessment signals, operator overrides, and
self-healing de-escalation heuristics.
"""
from __future__ import annotations

from collections import deque
from enum import Enum
from time import monotonic
from typing import Deque, Dict, Iterable, Optional

from pydantic import BaseModel, Field, NonNegativeFloat, PositiveInt, validator


class PersonaState(str, Enum):
    """Enumeration of supported personas."""

    HALSTON = "halston"
    SCARLET = "scarlet"


class ThreatSignal(BaseModel):
    """Normalized representation of a threat detection signal.

    Severity scores are expected to be in the range [0, 1]. The state machine
    uses the score to accumulate evidence for escalation. The source and
    description fields provide auditability for later review.
    """

    severity: NonNegativeFloat = Field(..., le=1.0)
    source: str = Field(..., min_length=1)
    description: str = Field(default="", max_length=512)
    timestamp: float = Field(default_factory=monotonic)

    @validator("description")
    def _trim_description(cls, value: str) -> str:  # pragma: no cover - defensive
        return value.strip()


class ReassuranceSignal(BaseModel):
    """Indicates an explicit human acknowledgement that the situation is safe."""

    confidence: NonNegativeFloat = Field(..., le=1.0)
    source: str = Field(..., min_length=1)
    timestamp: float = Field(default_factory=monotonic)


class ModeSwitchConfig(BaseModel):
    """Tunable parameters for the persona state machine."""

    escalate_threshold: float = Field(
        default=0.6,
        ge=0.0,
        le=1.0,
        description="Threat severity required to consider escalation.",
    )
    deescalate_threshold: float = Field(
        default=0.25,
        ge=0.0,
        le=1.0,
        description="Cumulative threat score below which de-escalation is allowed.",
    )
    sustained_escalation_count: PositiveInt = Field(
        default=2,
        description=(
            "Number of consecutive high severity signals required before switching "
            "to SCARLET."
        ),
    )
    sustained_reassurance_count: PositiveInt = Field(
        default=3,
        description=(
            "Number of consecutive reassurance signals required before returning to "
            "HALSTON."
        ),
    )
    lookback_window: PositiveInt = Field(
        default=10,
        description="Number of recent signals kept for rolling computation.",
    )
    cooldown_seconds: NonNegativeFloat = Field(
        default=30.0,
        description="Minimum time required between persona switches.",
    )

    @validator("deescalate_threshold")
    def _check_deescalate_threshold(cls, value: float, values: Dict[str, float]) -> float:
        escalate_threshold = values.get("escalate_threshold", 0.6)
        if value > escalate_threshold:
            raise ValueError("De-escalate threshold must not exceed escalate threshold.")
        return value


class PersonaStateMachine:
    """Finite state machine managing persona transitions.

    The machine accumulates threat and reassurance evidence over a sliding
    window. Once thresholds defined in :class:`ModeSwitchConfig` are met, the
    persona is switched. Manual overrides always take precedence.
    """

    def __init__(
        self,
        *,
        config: Optional[ModeSwitchConfig] = None,
        state: PersonaState = PersonaState.HALSTON,
    ) -> None:
        self.config = config or ModeSwitchConfig()
        self._state: PersonaState = state
        self._threat_signals: Deque[ThreatSignal] = deque(maxlen=self.config.lookback_window)
        self._reassurance_signals: Deque[ReassuranceSignal] = deque(
            maxlen=self.config.lookback_window
        )
        self._last_switch_time: float = monotonic()
        self._manual_override: Optional[PersonaState] = None

    @property
    def state(self) -> PersonaState:
        """Return the current persona state, honoring manual overrides."""

        return self._manual_override or self._state

    def set_manual_override(self, persona: Optional[PersonaState]) -> None:
        """Force the persona to a specific state.

        Passing ``None`` clears the manual override and resumes automatic
        switching.
        """

        self._manual_override = persona
        if persona is not None:
            self._state = persona
            self._last_switch_time = monotonic()

    def register_threat(self, signal: ThreatSignal) -> PersonaState:
        """Register a new threat signal and evaluate state transitions."""

        self._threat_signals.append(signal)
        return self._evaluate_state()

    def register_reassurance(self, signal: ReassuranceSignal) -> PersonaState:
        """Register a reassurance signal from a trusted operator."""

        self._reassurance_signals.append(signal)
        return self._evaluate_state()

    def consume_bulk_signals(
        self, *, threats: Iterable[ThreatSignal] = (), reassurances: Iterable[ReassuranceSignal] = ()
    ) -> PersonaState:
        """Consume a batch of signals prior to evaluating state transitions."""

        for threat in threats:
            self._threat_signals.append(threat)
        for reassurance in reassurances:
            self._reassurance_signals.append(reassurance)
        return self._evaluate_state()

    # Internal helpers -------------------------------------------------

    def _evaluate_state(self) -> PersonaState:
        if self._manual_override is not None:
            return self._manual_override

        now = monotonic()
        if now - self._last_switch_time < self.config.cooldown_seconds:
            # Within cooldown period; do not allow automatic switching but keep
            # collecting evidence for later.
            return self._state

        if self._should_escalate():
            self._state = PersonaState.SCARLET
            self._last_switch_time = now
            self._reassurance_signals.clear()
        elif self._should_deescalate():
            self._state = PersonaState.HALSTON
            self._last_switch_time = now
            self._threat_signals.clear()
        return self._state

    def _should_escalate(self) -> bool:
        if len(self._threat_signals) < self.config.sustained_escalation_count:
            return False
        recent = list(self._threat_signals)[-self.config.sustained_escalation_count :]
        high_severity = [sig for sig in recent if sig.severity >= self.config.escalate_threshold]
        if len(high_severity) < self.config.sustained_escalation_count:
            return False
        aggregate = sum(sig.severity for sig in recent) / len(recent)
        return aggregate >= self.config.escalate_threshold

    def _should_deescalate(self) -> bool:
        if len(self._reassurance_signals) < self.config.sustained_reassurance_count:
            return False
        recent = list(self._reassurance_signals)[-self.config.sustained_reassurance_count :]
        avg_confidence = sum(sig.confidence for sig in recent) / len(recent)
        if avg_confidence < self.config.deescalate_threshold:
            return False
        if not self._threat_signals:
            return True
        cumulative_threat = sum(sig.severity for sig in self._threat_signals) / len(
            self._threat_signals
        )
        return cumulative_threat <= self.config.deescalate_threshold
