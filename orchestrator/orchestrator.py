"""Core orchestrator wiring HALCYON personas, trust, and Home Assistant."""
from __future__ import annotations

import logging
import time
from dataclasses import dataclass
from typing import Dict, Optional, cast

from ha_adapter.intents.intent_router import IntentContext, IntentResult, IntentRouter
from ha_adapter.intents.intent_media import MediaIntentHandler
from halston.runtime.halston_agent import HalstonAgent
from orchestrator.context.session_state import SessionState, SessionStore
from orchestrator.logging.event_bus import EventBus
from orchestrator.mode_switching.state_machine import (
    PersonaState,
    PersonaStateMachine,
    ReassuranceSignal,
    ThreatSignal,
)
from orchestrator.policy_engine.access_control import AccessDecision
from orchestrator.policy_engine.trust_scoring import Role, TrustDecision, TrustInputs, TrustScorer
from orchestrator.routing.message_router import IntentClassification, MessageRouter
from scarlet.escalation_protocols.scarlet_agent import ScarletAgent
from speakerid.identity_resolver import IdentityResolver

Logger = logging.getLogger(__name__)


@dataclass
class OrchestratorDependencies:
    """Container for orchestrator runtime dependencies."""

    identity_resolver: IdentityResolver
    trust_scorer: TrustScorer
    message_router: MessageRouter
    intent_router: IntentRouter
    state_machine: PersonaStateMachine
    halston_agent: HalstonAgent
    scarlet_agent: ScarletAgent
    media_handler: Optional[MediaIntentHandler] = None


class Orchestrator:
    """Primary runtime for coordinating personas and intent execution."""

    def __init__(
        self,
        deps: OrchestratorDependencies,
        *,
        session_store: Optional[SessionStore] = None,
        event_bus: Optional[EventBus] = None,
    ) -> None:
        self._identity_resolver = deps.identity_resolver
        self._trust_scorer = deps.trust_scorer
        self._message_router = deps.message_router
        self._intent_router = deps.intent_router
        self._state_machine = deps.state_machine
        self._halston = deps.halston_agent
        self._scarlet = deps.scarlet_agent
        self.sessions = session_store or SessionStore(redis_url="redis://localhost:6379/0")
        self.events = event_bus or EventBus()
        if deps.media_handler is not None and getattr(self._intent_router, "_media", None) is None:
            self._intent_router._media = deps.media_handler  # type: ignore[attr-defined]

    # ------------------------------------------------------------------
    def process(
        self,
        user_text: str,
        speaker_temp_id: str,
        *,
        room_hint: Optional[str] = None,
        conversation_router: Optional[object] = None,
        output_router: Optional[object] = None,
    ) -> tuple[str, str]:
        """Process a text request and return the response along with persona label.

        Parameters
        ----------
        user_text:
            User input text.
        speaker_temp_id:
            Temporary speaker identifier.
        room_hint:
            Optional room identifier where wakeword was detected.
        conversation_router:
            Optional ConversationRouter instance for room selection and routing.
        output_router:
            Optional OutputRouter instance for TTS output routing.
        """
        if not user_text.strip():
            raise ValueError("user_text must be non-empty")

        stable_uuid, role_hint = self._identity_resolver.resolve(speaker_temp_id, voice_prob=1.0)
        session = self.sessions.load(stable_uuid, speaker_temp_id)
        now = time.time()

        inputs = TrustInputs(
            speaker_id=stable_uuid,
            voice_match=session.voice_confidence,
            face_match=session.face_confidence,
            prior_score=session.last_trust,
            context_mode=session.context_mode,
            reassurance=session.reassurance,
            threat=session.threat,
            last_update_ts=session.last_seen_ts,
            now_ts=now,
        )
        decision = self._trust_scorer.score(inputs, identity_role_hint=self._normalize_role(role_hint))
        persona = self._select_persona(session, decision)

        classification = self._message_router.classify(user_text, decision.role)
        intent_result: Optional[IntentResult] = None
        if classification.intent:
            intent_result = self._dispatch_intent(
                classification,
                decision,
                session,
                speaker_temp_id,
                persona,
            )

        response = self._render_response(
            session=session,
            persona=persona,
            user_text=user_text,
            classification=classification,
            intent_result=intent_result,
        )

        success = intent_result.ok if intent_result is not None else True
        session.last_trust = decision.score
        session.last_persona = persona.name
        session.last_intent = classification.intent
        session.last_response = response
        session.conversation_turn += 1
        self.sessions.save(session, stable_uuid, speaker_temp_id)

        self._publish_events(
            session=session,
            decision=decision,
            classification=classification,
            success=success,
            persona=persona,
            user_text=user_text,
        )

        # Multi-room routing (if routers are provided)
        if conversation_router and output_router:
            try:
                # Determine active room
                room_id = conversation_router.select_active_room(stable_uuid, speaker_temp_id, room_hint)

                # Check if speech is allowed
                if conversation_router.can_speak_in(room_id, persona.name):
                    # Generate TTS audio
                    from services.voice_pipeline.tts_engine import TTSEngine

                    tts = TTSEngine()
                    audio = tts.synth(persona=persona.name, text=response)

                    # Route via output router
                    output_router.route(persona.name, stable_uuid, room_id, audio)

                # Update last room and publish active room event
                conversation_router.update_last_room(stable_uuid, room_id)
            except Exception:
                # Routing failures should not break the orchestrator
                Logger.exception("Failed to route TTS output")

        persona_label = persona.name
        return response, persona_label

    # ------------------------------------------------------------------
    def _normalize_role(self, hint: Optional[str]) -> Optional[Role]:
        if hint in {"owner", "household", "guest", "unknown"}:
            return cast(Role, hint)
        return None

    def _select_persona(self, session: SessionState, decision: TrustDecision) -> PersonaState:
        persona = self._state_machine.state
        source = "state_machine"
        if decision.persona_bias == "SCARLET":
            severity = min(1.0, 0.4 + (100.0 - decision.score) / 100.0)
            persona = self._state_machine.register_threat(
                ThreatSignal(severity=severity, source="trust_bias", description="Trust bias escalation"),
            )
            source = "trust_bias"
        elif decision.persona_bias == "HALSTON":
            confidence = min(1.0, 0.4 + decision.score / 150.0)
            persona = self._state_machine.register_reassurance(
                ReassuranceSignal(confidence=confidence, source="trust_bias"),
            )
            source = "trust_bias"
        if decision.allow_sensitive is False and persona is PersonaState.SCARLET and decision.persona_bias != "SCARLET":
            persona = self._state_machine.register_reassurance(
                ReassuranceSignal(confidence=0.6, source="sensitivity_guard"),
            )
            source = "sensitivity_guard"
        self.events.publish(
            "orch/active_persona",
            {
                "persona": persona.value,
                "source": source,
                "conversation_turn": session.conversation_turn,
                "speaker_uuid": session.speaker_uuid,
            },
        )
        return persona

    def _dispatch_intent(
        self,
        classification: IntentClassification,
        decision: TrustDecision,
        session: SessionState,
        speaker_temp_id: str,
        persona: PersonaState,
    ) -> IntentResult:
        context = IntentContext(
            role=decision.role,
            allow_sensitive=decision.allow_sensitive,
            mode=session.context_mode,
            speaker_uuid=session.speaker_uuid,
            session_id=speaker_temp_id,
            persona=persona.value,
        )
        try:
            return self._intent_router.handle(classification.intent or "", classification.slots, context)
        except Exception:  # pragma: no cover - operational safeguard
            Logger.exception("Intent handler failure: intent=%s", classification.intent)
            return IntentResult(ok=False, spoken="I encountered an internal error handling that request.")

    def _render_response(
        self,
        *,
        session: SessionState,
        persona: PersonaState,
        user_text: str,
        classification: IntentClassification,
        intent_result: Optional[IntentResult],
    ) -> str:
        agent = self._halston if persona is PersonaState.HALSTON else self._scarlet
        summary = {
            "speaker_uuid": session.speaker_uuid,
            "context_mode": session.context_mode,
            "conversation_turn": session.conversation_turn,
            "last_trust": session.last_trust,
        }
        metadata: Dict[str, object] = {
            "session": summary,
            "slots": classification.slots,
            "intent_confidence": classification.confidence,
        }
        if intent_result is not None:
            metadata["intent_result"] = intent_result.dict()
        if intent_result is None:
            return agent.generate_response(user_text, intent=None, metadata=metadata)
        if intent_result.ok:
            response = agent.generate_response(user_text, intent=classification.intent, metadata=metadata)
            spoken = intent_result.spoken.strip()
            return f"{response} {spoken}".strip()
        denial_reason = intent_result.spoken or "The request could not be completed."
        denial = AccessDecision(allowed=False, reason=denial_reason, required_trust=None, speaker_trust=None)
        return agent.build_denied_response(denial)

    def _publish_events(
        self,
        *,
        session: SessionState,
        decision: TrustDecision,
        classification: IntentClassification,
        success: bool,
        persona: PersonaState,
        user_text: str,
    ) -> None:
        self.events.publish(
            "orch/trust",
            {
                "score": round(decision.score, 2),
                "role": decision.role,
                "allow_sensitive": decision.allow_sensitive,
                "persona_bias": decision.persona_bias,
                "speaker_uuid": session.speaker_uuid,
            },
        )
        self.events.publish(
            "orch/intent",
            {
                "intent": classification.intent,
                "slots": classification.slots,
                "success": success if classification.intent else None,
                "persona": persona.value,
                "excerpt": user_text[:160],
                "speaker_uuid": session.speaker_uuid,
            },
        )
