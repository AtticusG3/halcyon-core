"""Trust scoring logic supporting HALCYON access and persona decisions."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, Literal, Optional
import time


Role = Literal["owner", "household", "guest", "unknown"]


@dataclass(frozen=True)
class TrustInputs:
    """Inputs that contribute to the dynamic trust evaluation."""

    speaker_id: Optional[str]
    voice_match: Optional[float] = None
    face_match: Optional[float] = None
    prior_score: float = 0.0
    context_mode: Literal["home", "away", "night", "maintenance", "incident"] = "home"
    reassurance: float = 0.0
    threat: float = 0.0
    last_update_ts: float = 0.0
    now_ts: float = time.time()


@dataclass
class TrustDecision:
    """Outcome from trust scoring including persona bias and access hints."""

    score: float
    role: Role
    allow_sensitive: bool
    persona_bias: Literal["HALSTON", "SCARLET", "neutral"]
    notes: str


class TrustScorer:
    """Numeric trust model with hysteresis and contextual adjustments."""

    BASE_GUEST = 15.0
    OWNER_THRESH = 75.0
    HOUSEHOLD_THRESH = 55.0
    GUEST_MAX = 35.0
    COOLDOWN_SEC = 20.0
    HYSTERESIS_BAND = 6.0

    CONTEXT_PENALTIES: Dict[str, float] = {
        "home": 0.0,
        "maintenance": -5.0,
        "night": +8.0,
        "away": +15.0,
        "incident": +25.0,
    }

    def score(self, inp: TrustInputs, identity_role_hint: Optional[Role]) -> TrustDecision:
        """Calculate a trust decision from the current sensory and identity inputs."""

        voice = inp.voice_match or 0.0
        face = inp.face_match or 0.0
        id_strength = max(voice, face) * 100.0

        s = max(self.BASE_GUEST, id_strength)
        s -= self.CONTEXT_PENALTIES.get(inp.context_mode, 0.0)
        s += max(-20.0, min(20.0, inp.reassurance))
        s -= max(0.0, min(30.0, inp.threat))

        dt = (inp.now_ts - inp.last_update_ts) if inp.last_update_ts else 9999
        if dt < self.COOLDOWN_SEC and abs(s - inp.prior_score) < self.HYSTERESIS_BAND:
            s = inp.prior_score

        s = max(0.0, min(100.0, s))

        role: Role = "unknown"
        if s >= self.OWNER_THRESH:
            role_hint = identity_role_hint or "household"
            role = "owner" if role_hint == "owner" else "household"
        elif s >= self.HOUSEHOLD_THRESH:
            role = "household"
        elif s <= self.GUEST_MAX:
            role = "guest"
        else:
            role = "guest"

        allow_sensitive = (
            role in {"owner", "household"} and inp.context_mode in {"home", "maintenance"}
        )
        if inp.context_mode == "night" and role == "owner" and voice >= 0.80:
            allow_sensitive = True

        persona_bias: Literal["HALSTON", "SCARLET", "neutral"] = "neutral"
        if inp.threat >= 15.0 or inp.context_mode in {"away", "incident"}:
            persona_bias = "SCARLET"
        elif role in {"owner", "household"} and inp.threat <= 5.0:
            persona_bias = "HALSTON"

        return TrustDecision(
            score=s,
            role=role,
            allow_sensitive=allow_sensitive,
            persona_bias=persona_bias,
            notes=(
                f"id_strength={id_strength:.1f}, ctx={inp.context_mode}, "
                f"threat={inp.threat:.1f}, reassure={inp.reassurance:.1f}"
            ),
        )

