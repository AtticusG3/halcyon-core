"""Glue between the voice pipeline and the HALCYON orchestrator."""
from __future__ import annotations

import logging
from typing import Callable, Optional

from orchestrator.orchestrator import Orchestrator
from services.voice_pipeline.conversation_router import ConversationRouter
from services.voice_pipeline.input_mux import InputMux
from services.voice_pipeline.output_router import OutputRouter
from services.voice_pipeline.stt_engine import STTEngine, TranscriptEvent
from services.voice_pipeline.tts_engine import Persona, TTSEngine
from services.voice_pipeline.wakeword_bus import WakeEvent, WakewordBus

_LOGGER = logging.getLogger(__name__)


class VoiceLoop:
    """Reference implementation of the streaming voice loop with multi-room support."""

    def __init__(
        self,
        orchestrator: Orchestrator,
        *,
        playback_callback: Optional[Callable[[bytes, Persona], None]] = None,
        stt_engine: Optional[STTEngine] = None,
        tts_engine: Optional[TTSEngine] = None,
        default_temp_speaker: str = "mic:default",
        wakeword_bus: Optional[WakewordBus] = None,
        conversation_router: Optional[ConversationRouter] = None,
        output_router: Optional[OutputRouter] = None,
        input_mux: Optional[InputMux] = None,
    ) -> None:
        """Initialize the voice loop.

        Parameters
        ----------
        orchestrator:
            Orchestrator instance for processing user input.
        playback_callback:
            Optional legacy callback for playback (backward compatibility).
            If output_router is provided, this is not used.
        stt_engine:
            STTEngine instance. If None, creates a default one.
        tts_engine:
            TTSEngine instance. If None, creates a default one.
        default_temp_speaker:
            Default temporary speaker identifier.
        wakeword_bus:
            WakewordBus instance for wakeword events. If None, multi-room features are disabled.
        conversation_router:
            ConversationRouter instance for room selection. Required if output_router is provided.
        output_router:
            OutputRouter instance for TTS routing. If None, uses legacy playback_callback.
        input_mux:
            InputMux instance for frame routing. If None, uses direct STT push.
        """
        self._orchestrator = orchestrator
        self._playback = playback_callback
        self._speaker_temp_id = default_temp_speaker
        self._stt = stt_engine or STTEngine(on_transcript=self._on_transcript)
        self._tts = tts_engine or TTSEngine()
        self._wakeword_bus = wakeword_bus
        self._conversation_router = conversation_router
        self._output_router = output_router
        self._input_mux = input_mux

        # Track current room hint from wakeword
        self._current_room_hint: Optional[str] = None
        self._current_mic_id: Optional[str] = None

        # Subscribe to wakeword events if bus is available
        if self._wakeword_bus:
            self._wakeword_bus.subscribe(self._on_wake_event)

    # ------------------------------------------------------------------
    def start(self) -> None:
        """Start streaming audio ingestion."""

        self._stt.start()

    def stop(self) -> None:
        """Stop streaming audio ingestion."""

        self._stt.stop()

    # ------------------------------------------------------------------
    def _on_wake_event(self, event: WakeEvent) -> None:
        """Handle wakeword detection event."""
        from services.voice_pipeline.room_registry import RoomRegistry

        # Get room for this mic
        # We need room_registry - it should be available via conversation_router
        if self._conversation_router:
            room_registry = self._conversation_router._room_registry
            room_id = room_registry.get_room_for_mic(event.mic_id)
            if room_id:
                self._current_room_hint = room_id
                self._current_mic_id = event.mic_id
                _LOGGER.debug("Wake event from mic %s (room %s)", event.mic_id, room_id)

    def push_pcm(self, frame_20ms: bytes, *, speaker_temp_id: Optional[str] = None, mic_id: Optional[str] = None) -> None:
        """Feed a 20ms PCM frame from ``speaker_temp_id`` into the loop.

        Parameters
        ----------
        frame_20ms:
            20ms PCM audio frame.
        speaker_temp_id:
            Optional temporary speaker identifier.
        mic_id:
            Optional microphone identifier (for multi-room routing).
        """
        if speaker_temp_id is not None:
            self._speaker_temp_id = speaker_temp_id

        # If input_mux is available, use it for routing
        if self._input_mux and mic_id:
            self._input_mux.push(mic_id, frame_20ms)
        else:
            # Legacy direct push
            self._stt.push_audio(frame_20ms)

    # ------------------------------------------------------------------
    def _on_transcript(self, event: TranscriptEvent) -> None:
        text = event.text.strip()
        if not text:
            return

        # Get room hint from current wakeword context
        room_hint = self._current_room_hint

        try:
            # Process with orchestrator (room_hint will be used if orchestrator supports it)
            response_text, persona = self._orchestrator.process(text, self._speaker_temp_id, room_hint=room_hint)
        except TypeError:
            # Backward compatibility: orchestrator doesn't support room_hint yet
            response_text, persona = self._orchestrator.process(text, self._speaker_temp_id)
        except Exception:  # pragma: no cover - operational safety net
            _LOGGER.exception("Orchestrator failure while handling transcript")
            return

        try:
            audio = self._tts.synth(persona=persona, text=response_text)

            # Route output via OutputRouter if available
            if self._output_router and self._conversation_router:
                # Determine room for output
                # Get UUID from temp_id (would need identity resolver, simplified for now)
                uuid = None  # Would be resolved from temp_id in full implementation

                # Select active room
                room_id = self._conversation_router.select_active_room(uuid, self._speaker_temp_id, room_hint)

                # Route via output router
                self._output_router.route(persona, uuid, room_id, audio)
            elif self._playback:
                # Legacy playback callback
                self._playback(audio, persona)
            else:
                _LOGGER.warning("No output routing available - dropping audio")

            # Release mic session after utterance
            if self._input_mux and self._current_mic_id:
                self._input_mux.release_session(self._current_mic_id)
                self._current_mic_id = None
                self._current_room_hint = None

        except Exception:  # pragma: no cover - playback failures should not crash loop
            _LOGGER.exception("TTS or playback failure")
