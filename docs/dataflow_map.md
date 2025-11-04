# HALCYON System Dataflow Map

This document is the authoritative wiring map for the HALCYON core loop. It captures signal flow, session state feedback, and external bindings without narrative prose.

## Core Loop

```
🧭 HALCYON System Dataflow (Core Loop)

┌──────────────────────────┐
 Voice / Text Input          │                          │
 (Mic, Chat UI, Discord) --->│  Orchestrator.process()   │
                             │                          │
                             └─────────────┬────────────┘
                                           │
                                           │ text + speaker_temp_id
                                           ▼
                      ┌──────────────────────────────┐
                      │ speakerid.IdentityResolver    │
                      │  - maps temp speaker → UUID   │
                      │  - returns (uuid, role_hint)  │
                      └─────────────┬────────────────┘
                                    │ stable identity (or None)
                                    │ voice probability (later STT)
                                    ▼
                      ┌──────────────────────────────┐
                      │ policy_engine.TrustScorer     │
                      │  Inputs:                      │
                      │   - voice_match (float)       │
                      │   - context_mode (home/night) │
                      │   - threat / reassurance      │
                      │   - previous trust score      │
                      │                               │
                      │  Output: TrustDecision        │
                      │   - score: 0..100             │
                      │   - role: owner/household/... │
                      │   - allow_sensitive: bool     │
                      │   - persona_bias: HALSTON/…   │
                      └─────────────┬────────────────┘
                                    │ TrustDecision
                                    ▼
                          ┌─────────────────────┐
                          │ Persona Selector     │
                          │ (mode_switching FSM) │
                          └─────────────┬────────┘
                                        │ active_persona
                                        ▼
                            ┌───────────────────────┐
                            │ Persona Agent         │
   (if persona == HALSTON) → │ halston_agent.py      │
   (if persona == SCARLET) → │ scarlet_agent.py      │
                            └─────────────┬─────────┘
                                          │
                                          │ selects tone + phrasing style
                                          │
                                          ▼
                          ┌──────────────────────────────────┐
                          │ Intent Determination              │
                          │  (lightweight keyword → intent)   │
                          └─────────────┬────────────────────┘
                                        │ intent + slots
                                        ▼
                          ┌─────────────────────────────────┐
                          │ ha_adapter.IntentRouter          │
                          │  - enforces allow_sensitive      │
                          │  - returns spoken + ok state     │
                          └─────────────┬───────────────────┘
                                        │ service_call request
                                        ▼
                        ┌───────────────────────────────────────┐
                        │ services.event_bridge.HAMQTTBridge     │
                        │  publish → MQTT → Home Assistant       │
                        └─────────────┬─────────────────────────┘
                                      │ MQTT topic: halcyon/ha/call
                                      ▼
                     ┌────────────────────────────────────────────┐
                     │ Home Assistant Automation (yaml)            │
                     │ runs real HA services (light, lock, etc.)  │
                     └────────────────────────────────────────────┘
```

## State & Feedback Loops

- Home Assistant events publish to `halcyon/ha/event/*`, refreshing context mode, reassurance, and threat inputs.
- Session memory retains trust hysteresis, persona stickiness, and conversation traces per speaker session.
- Speaker-ID updates are written via the identity resolver, aligning future requests with stable UUIDs.

## Future Modules (Pre-wired Boundaries)

| Module | Status | Activation Trigger |
| --- | --- | --- |
| Whisper local STT | Pending | Replace text input with speech transcription |
| XTTS v2 Voice Synthesis | Pending | Pipe persona responses to playback hardware |
| CV feeds (Frigate / CompreFace / Nextcloud) | Pending | Emit reassurance/threat signals into trust scorer |
| Memory embeddings / Chroma | Pending | Persist conversation state beyond short-term buffer |

## Persona Behaviors

- **SCARLET** activates when persona bias or threat metrics exceed safe thresholds, tightening sensitive actions and routing optional alerts (e.g., `halcyon/security/alert`).
- **HALSTON** remains default, offering calm assistance while respecting trust gates and context-specific restrictions.

## Diagnostic Topics

- `halcyon/orch/active_persona`
- `halcyon/orch/intent`
- `halcyon/orch/trust`

These topics provide observability for UI panels and external monitors.
