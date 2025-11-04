"""Unit tests for the HALCYON orchestrator pipeline."""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Dict, List, Mapping, Optional, Tuple
from uuid import uuid4

import pytest

import sys

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from ha_adapter.intents.intent_router import IntentRouter
from halston.runtime.halston_agent import HalstonAgent
from orchestrator.context.session_state import SessionStore
from orchestrator.orchestrator import Orchestrator, OrchestratorDependencies
from orchestrator.policy_engine.trust_scoring import TrustScorer
from orchestrator.routing.message_router import MessageRouter, RouterConfig
from orchestrator.mode_switching.state_machine import ModeSwitchConfig, PersonaStateMachine
from scarlet.escalation_protocols.scarlet_agent import ScarletAgent


class DummyMQTTBridge:
    """Minimal MQTT bridge stub for intent routing tests."""

    def __init__(self, *, responses: Optional[Mapping[Tuple[str, str], bool]] = None) -> None:
        self.calls: List[Tuple[str, str, Dict[str, object]]] = []
        self._responses = dict(responses or {})

    def call_service(self, domain: str, service: str, data: Dict[str, object]) -> bool:
        self.calls.append((domain, service, dict(data)))
        return self._responses.get((domain, service), True)


class TelemetryCollector:
    """Captures orchestrator telemetry published via EventBus."""

    def __init__(self) -> None:
        self.messages: List[Tuple[str, Dict[str, object]]] = []

    def publish(self, topic_suffix: str, payload: Dict[str, object]) -> None:
        self.messages.append((topic_suffix, dict(payload)))

    def last_for(self, topic_suffix: str) -> Optional[Dict[str, object]]:
        for topic, payload in reversed(self.messages):
            if topic == topic_suffix:
                return payload
        return None


@dataclass
class FakeIdentityResolver:
    """Deterministic identity resolver for unit tests."""

    mapping: Dict[str, Tuple[Optional[str], Optional[str]]]

    def resolve(self, speaker_temp_id: str, voice_prob: float) -> Tuple[Optional[str], Optional[str]]:
        return self.mapping.get(speaker_temp_id, (None, None))


@pytest.fixture
def orchestrator_factory():
    """Provide a factory for orchestrator instances with isolated dependencies."""

    def _factory(
        *,
        identities: Optional[Mapping[str, Tuple[Optional[str], Optional[str]]]] = None,
        router_config: Optional[RouterConfig] = None,
    ) -> Tuple[
        Orchestrator,
        SessionStore,
        FakeIdentityResolver,
        TelemetryCollector,
        DummyMQTTBridge,
    ]:
        collector = TelemetryCollector()
        session_store = SessionStore(redis_url=f"memory://{uuid4()}")
        identity_resolver = FakeIdentityResolver(mapping=dict(identities or {}))
        mqtt_bridge = DummyMQTTBridge()
        intent_router = IntentRouter(mqtt_bridge=mqtt_bridge)
        message_router = MessageRouter(router_config)
        state_machine = PersonaStateMachine(
            config=ModeSwitchConfig(
                cooldown_seconds=0.0,
                sustained_escalation_count=1,
                sustained_reassurance_count=1,
            )
        )
        deps = OrchestratorDependencies(
            identity_resolver=identity_resolver,
            trust_scorer=TrustScorer(),
            message_router=message_router,
            intent_router=intent_router,
            state_machine=state_machine,
            halston_agent=HalstonAgent(),
            scarlet_agent=ScarletAgent(),
        )
        orchestrator = Orchestrator(deps, session_store=session_store, event_bus=collector)
        return orchestrator, session_store, identity_resolver, collector, mqtt_bridge

    return _factory


def _set_voice(store: SessionStore, stable_uuid: Optional[str], temp_id: str, prob: float) -> None:
    state = store.load(stable_uuid, temp_id)
    state.voice_confidence = prob
    store.save(state, stable_uuid, temp_id)


def test_process_known_owner(orchestrator_factory) -> None:
    orchestrator, store, _, collector, _ = orchestrator_factory(
        identities={"speaker-owner": ("owner-uuid", "owner")}
    )
    _set_voice(store, "owner-uuid", "speaker-owner", 0.95)

    response, persona = orchestrator.process("Turn on the kitchen light", "speaker-owner")

    assert "Halston here" in response
    assert "Done." in response
    assert persona == "HALSTON"
    trust_event = collector.last_for("orch/trust")
    assert trust_event is not None
    assert trust_event["role"] == "owner"
    assert trust_event["allow_sensitive"] is True
    persona_event = collector.last_for("orch/active_persona")
    assert persona_event is not None
    assert persona_event["persona"] == "halston"


def test_process_household_member(orchestrator_factory) -> None:
    orchestrator, store, _, collector, mqtt_bridge = orchestrator_factory(
        identities={"speaker-house": ("household-uuid", "household")}
    )
    _set_voice(store, "household-uuid", "speaker-house", 0.8)

    response, persona = orchestrator.process("Lock the back door", "speaker-house")

    assert "Halston here" in response
    assert "Locked." in response
    assert persona == "HALSTON"
    assert mqtt_bridge.calls[-1][0:2] == ("lock", "lock")
    trust_event = collector.last_for("orch/trust")
    assert trust_event is not None
    assert trust_event["role"] == "household"
    assert trust_event["allow_sensitive"] is True


def test_process_guest_denied_sensitive_action(orchestrator_factory) -> None:
    orchestrator, store, _, collector, _ = orchestrator_factory(
        identities={"speaker-guest": (None, "guest")}
    )
    _set_voice(store, None, "speaker-guest", 0.3)

    response, persona = orchestrator.process("Please unlock the front door", "speaker-guest")

    assert "I must decline" in response
    assert "That function is not available right now." in response
    assert persona == "HALSTON"
    trust_event = collector.last_for("orch/trust")
    assert trust_event is not None
    assert trust_event["role"] == "guest"
    persona_event = collector.last_for("orch/active_persona")
    assert persona_event is not None
    assert persona_event["persona"] == "halston"


def test_process_unknown_voice_defaults_to_halston(orchestrator_factory) -> None:
    orchestrator, store, _, collector, _ = orchestrator_factory()
    _set_voice(store, None, "unknown-speaker", 0.1)

    response, persona = orchestrator.process("Hello there", "unknown-speaker")

    assert "Halston here" in response
    assert persona == "HALSTON"
    trust_event = collector.last_for("orch/trust")
    assert trust_event is not None
    assert trust_event["role"] == "guest"


def test_process_away_mode_escalates_to_scarlet(orchestrator_factory) -> None:
    orchestrator, store, _, collector, _ = orchestrator_factory(
        identities={"speaker-away": ("owner-uuid", "owner")}
    )
    store.touch_context("owner-uuid", "speaker-away", "away")
    _set_voice(store, "owner-uuid", "speaker-away", 0.95)

    response, persona = orchestrator.process("Turn on the living room light", "speaker-away")

    assert "Scarlet assuming control" in response
    assert persona == "SCARLET"
    persona_event = collector.last_for("orch/active_persona")
    assert persona_event is not None
    assert persona_event["persona"] == "scarlet"


def test_process_incident_mode_prefers_scarlet(orchestrator_factory) -> None:
    orchestrator, store, _, collector, _ = orchestrator_factory(
        identities={"speaker-incident": ("owner-uuid", "owner")}
    )
    store.touch_context("owner-uuid", "speaker-incident", "incident")
    _set_voice(store, "owner-uuid", "speaker-incident", 0.9)

    response, persona = orchestrator.process("We need help there is an intruder", "speaker-incident")

    assert "Scarlet assuming control" in response
    assert persona == "SCARLET"
    persona_event = collector.last_for("orch/active_persona")
    assert persona_event is not None
    assert persona_event["persona"] == "scarlet"
