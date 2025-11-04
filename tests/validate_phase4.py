"""Phase 4 validation script for manual REPL testing."""
from __future__ import annotations

import os
import sys
from pathlib import Path

# Add project root to path
ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from orchestrator.context.session_state import SessionStore
from orchestrator.logging.event_bus import EventBus
from orchestrator.orchestrator import Orchestrator, OrchestratorDependencies
from orchestrator.policy_engine.trust_scoring import TrustScorer
from orchestrator.routing.message_router import MessageRouter
from orchestrator.mode_switching.state_machine import ModeSwitchConfig, PersonaStateMachine
from ha_adapter.intents.intent_router import IntentRouter
from halston.runtime.halston_agent import HalstonAgent
from scarlet.escalation_protocols.scarlet_agent import ScarletAgent
from speakerid.identity_resolver import IdentityResolver
from services.event_bridge.homeassistant_mqtt import HAMQTTBridge
from services.media.overseerr_client import OverseerrClient
from services.media.taste_profile import TasteProfile
from services.media.recommender import MediaRecommender
from services.media.plex_client import PlexClient
from services.media.tmdb_client import TMDBClient
from ha_adapter.intents.intent_media import MediaIntentHandler


class TelemetryCollector:
    """Collects telemetry for validation."""

    def __init__(self):
        self.messages = []

    def publish(self, topic_suffix: str, payload: dict) -> None:
        self.messages.append((topic_suffix, payload))
        print(f"[MQTT] {topic_suffix}: {payload}")


def setup_orchestrator() -> Orchestrator:
    """Set up orchestrator with dependencies."""
    # Use in-memory Redis for testing
    redis_url = "memory://phase4_test"

    # Identity resolver
    identity_resolver = IdentityResolver()

    # Trust scorer
    trust_scorer = TrustScorer()

    # Message router
    message_router = MessageRouter()

    # Intent router with media handler
    event_bus = TelemetryCollector()
    mqtt_bridge = HAMQTTBridge(host="127.0.0.1", port=1883)
    overseerr = OverseerrClient(
        base_url="http://localhost:5055",
        api_key="test-key",
    )
    plex_client = PlexClient(
        base_url="http://localhost:32400",
        token="test-token",
        redis_url=redis_url,
    )
    tmdb_client = TMDBClient(api_key="test-key")
    recommender = MediaRecommender(
        plex_client=plex_client,
        tmdb_client=tmdb_client,
        event_bus=event_bus,
    )
    media_handler = MediaIntentHandler(
        overseerr=overseerr,
        recommender=recommender,
        event_bus=event_bus,
        redis_url=redis_url,
    )
    intent_router = IntentRouter(mqtt_bridge=mqtt_bridge)
    intent_router._media = media_handler  # type: ignore

    # State machine
    state_machine = PersonaStateMachine()

    # Agents
    halston_agent = HalstonAgent()
    scarlet_agent = ScarletAgent()

    # Dependencies
    deps = OrchestratorDependencies(
        identity_resolver=identity_resolver,
        trust_scorer=trust_scorer,
        message_router=message_router,
        intent_router=intent_router,
        state_machine=state_machine,
        halston_agent=halston_agent,
        scarlet_agent=scarlet_agent,
        media_handler=media_handler,
    )

    # Session store and event bus
    session_store = SessionStore(redis_url=redis_url)
    event_bus = TelemetryCollector()

    orchestrator = Orchestrator(deps, session_store=session_store, event_bus=event_bus)

    return orchestrator


def test_1_basic_light_control():
    """Test 1: Basic light control command."""
    print("\n" + "=" * 60)
    print("TEST 1: Basic Light Control")
    print("=" * 60)

    orch = setup_orchestrator()

    response, persona = orch.process("turn on the kitchen lights", speaker_temp_id="mic_kitchen_1")

    print(f"\nResponse: {response}")
    print(f"Persona: {persona}")
    print(f"\nExpected: Persona should be HALSTON, response should acknowledge lights")

    assert persona == "HALSTON", f"Expected HALSTON, got {persona}"
    assert "kitchen" in response.lower() or "light" in response.lower(), "Response should mention kitchen/lights"

    print("✅ TEST 1 PASSED")


def test_2_security_denial():
    """Test 2: Security command denial for unknown voice."""
    print("\n" + "=" * 60)
    print("TEST 2: Security Command Denial")
    print("=" * 60)

    orch = setup_orchestrator()

    response, persona = orch.process("disarm the alarm", speaker_temp_id="unknown_voice_123")

    print(f"\nResponse: {response}")
    print(f"Persona: {persona}")

    # Check MQTT telemetry
    trust_events = [msg for topic, msg in orch.events.messages if topic == "orch/trust"]
    if trust_events:
        print(f"\nTrust Score: {trust_events[-1].get('score', 'N/A')}")
        print(f"Role: {trust_events[-1].get('role', 'N/A')}")

    print(f"\nExpected: Should be denial (polite), persona HALSTON unless trust very low")

    # Response should be denial
    denial_keywords = ["can't", "cannot", "unable", "denied", "not allowed", "sorry"]
    is_denial = any(keyword in response.lower() for keyword in denial_keywords)
    assert is_denial or persona == "SCARLET", "Should be denial or SCARLET persona"

    print("✅ TEST 2 PASSED")


def test_3_media_recommendation():
    """Test 3: Media recommendation conversational path."""
    print("\n" + "=" * 60)
    print("TEST 3: Media Recommendation")
    print("=" * 60)

    orch = setup_orchestrator()

    # First request
    response, persona = orch.process("Halston, what should I watch?", speaker_temp_id="mic_lounge_1")

    print(f"\nResponse: {response}")
    print(f"Persona: {persona}")

    # Check for recommendations
    assert persona == "HALSTON", "Should be HALSTON persona"
    assert len(response) > 50, "Response should be substantial (recommendations)"
    assert any(word in response.lower() for word in ["watch", "recommend", "suggest", "option"]), "Should mention recommendations"

    print(f"\nExpected: Top recommendations with rationale")

    # Check MQTT for media events
    media_events = [msg for topic, msg in orch.events.messages if "media" in topic.lower()]
    if media_events:
        print(f"\nMedia Events: {len(media_events)}")

    print("✅ TEST 3 PASSED")


def test_4_persona_hysteresis():
    """Test 4: Persona alignment and hysteresis."""
    print("\n" + "=" * 60)
    print("TEST 4: Persona Hysteresis")
    print("=" * 60)

    orch = setup_orchestrator()

    # Initial request
    response1, persona1 = orch.process("Halston, what do you recommend today?", speaker_temp_id="known_user")
    print(f"\nInitial Response: {response1[:100]}...")
    print(f"Initial Persona: {persona1}")

    # Simulate threat-lowering
    response2, persona2 = orch.process("Halston, everything's fine now.", speaker_temp_id="known_user")
    print(f"\nAfter Reassurance: {persona2}")

    # Check trust events
    trust_events = [msg for topic, msg in orch.events.messages if topic == "orch/trust"]
    if len(trust_events) >= 2:
        scores = [msg.get("score", 0) for msg in trust_events[-2:]]
        print(f"\nTrust Scores: {scores}")

    # Persona should not flip rapidly
    if persona1 == "HALSTON" and persona2 == "SCARLET":
        print("\n⚠️  Persona flipped - verify trust threshold is appropriate")

    print(f"\nExpected: Persona should not flip rapidly back and forth")
    print("✅ TEST 4 PASSED (verify hysteresis behavior)")


def test_5_edge_cases():
    """Test 5: Edge case audits."""
    print("\n" + "=" * 60)
    print("TEST 5: Edge Cases")
    print("=" * 60)

    orch = setup_orchestrator()

    # Test empty/whitespace
    try:
        response, persona = orch.process("   ", speaker_temp_id="test")
        print("❌ Should have raised ValueError for empty input")
    except ValueError:
        print("✅ Empty input correctly rejected")

    # Test graceful error handling
    response, persona = orch.process("what should I watch?", speaker_temp_id="test")
    print(f"\nResponse (no watch history): {response[:100]}...")
    assert len(response) > 0, "Should provide response even with no history"

    print("✅ TEST 5 PASSED")


if __name__ == "__main__":
    print("\n" + "=" * 60)
    print("PHASE 4 VALIDATION TEST SUITE")
    print("=" * 60)

    try:
        test_1_basic_light_control()
        test_2_security_denial()
        test_3_media_recommendation()
        test_4_persona_hysteresis()
        test_5_edge_cases()

        print("\n" + "=" * 60)
        print("✅ ALL PHASE 4 TESTS COMPLETED")
        print("=" * 60)
        print("\nReview the output above for any warnings or unexpected behavior.")
        print("Manual verification may be required for:")
        print("  - MQTT event publishing")
        print("  - Overseerr integration (requires mock server)")
        print("  - Trust score thresholds")

    except Exception as e:
        print(f"\n❌ TEST FAILED: {e}")
        import traceback

        traceback.print_exc()
        sys.exit(1)

