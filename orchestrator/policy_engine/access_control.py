"""Trust-gated access control for HALCYON intents."""
from __future__ import annotations

from enum import Enum
from typing import Dict, Optional

from pydantic import BaseModel, Field


class TrustLevel(int, Enum):
    """Trust hierarchy for speaker identities."""

    BLOCKED = 0
    GUEST = 1
    KNOWN = 2
    ADMIN = 3


class AccessDecision(BaseModel):
    """Represents the result of an access control evaluation."""

    allowed: bool
    reason: Optional[str] = None
    required_trust: TrustLevel | None = None
    speaker_trust: TrustLevel | None = None


class IntentPolicy(BaseModel):
    """Policy metadata describing the requirements for an intent."""

    name: str
    minimum_trust: TrustLevel = Field(default=TrustLevel.GUEST)
    allow_unrecognized: bool = Field(
        default=False,
        description="Whether anonymous speakers may trigger this intent if confidence is high.",
    )


class IntentRequest(BaseModel):
    """Normalized request passed into the policy engine."""

    intent_name: str
    speaker_id: Optional[str] = None
    confidence: float = Field(default=0.0, ge=0.0, le=1.0)
    metadata: Dict[str, object] = Field(default_factory=dict)


class SpeakerProfile(BaseModel):
    """Stored metadata regarding a speaker identity."""

    speaker_id: str
    trust_level: TrustLevel = TrustLevel.GUEST
    is_verified: bool = False


class AccessController:
    """Evaluates intents against trust policies and speaker profiles."""

    def __init__(
        self,
        *,
        intent_policies: Dict[str, IntentPolicy],
        speaker_directory: Dict[str, SpeakerProfile] | None = None,
        default_policy: IntentPolicy | None = None,
    ) -> None:
        self._intent_policies = intent_policies
        self._default_policy = default_policy or IntentPolicy(name="fallback", minimum_trust=TrustLevel.GUEST)
        self._speaker_directory: Dict[str, SpeakerProfile] = speaker_directory or {}

    def update_speaker(self, profile: SpeakerProfile) -> None:
        """Insert or update a speaker profile."""

        self._speaker_directory[profile.speaker_id] = profile

    def evaluate(self, request: IntentRequest) -> AccessDecision:
        """Evaluate an intent request against policy definitions."""

        policy = self._intent_policies.get(request.intent_name, self._default_policy)
        speaker_profile = (
            self._speaker_directory.get(request.speaker_id) if request.speaker_id else None
        )
        speaker_trust = speaker_profile.trust_level if speaker_profile else TrustLevel.BLOCKED

        if request.speaker_id is None and not policy.allow_unrecognized:
            if request.confidence < 0.85:
                return AccessDecision(
                    allowed=False,
                    reason="Unidentified speaker with insufficient confidence.",
                    required_trust=policy.minimum_trust,
                    speaker_trust=None,
                )

        if speaker_profile is None:
            if policy.allow_unrecognized and request.confidence >= 0.85:
                return AccessDecision(allowed=True, speaker_trust=None, required_trust=policy.minimum_trust)
            return AccessDecision(
                allowed=False,
                reason="Speaker not recognized.",
                required_trust=policy.minimum_trust,
                speaker_trust=None,
            )

        if speaker_trust < policy.minimum_trust:
            return AccessDecision(
                allowed=False,
                reason="Insufficient trust level.",
                required_trust=policy.minimum_trust,
                speaker_trust=speaker_trust,
            )

        if not speaker_profile.is_verified and policy.minimum_trust >= TrustLevel.ADMIN:
            return AccessDecision(
                allowed=False,
                reason="Administrative actions require verified identity.",
                required_trust=policy.minimum_trust,
                speaker_trust=speaker_trust,
            )

        return AccessDecision(allowed=True, speaker_trust=speaker_trust, required_trust=policy.minimum_trust)
