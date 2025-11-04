"""Glue between the voice pipeline and the HALCYON orchestrator."""
from __future__ import annotations

import logging
from typing import Callable, Optional

from orchestrator.orchestrator import Orchestrator
from services.voice_pipeline.stt_engine import STTEngine, TranscriptEvent
from services.voice_pipeline.tts_engine import Persona, TTSEngine

_LOGGER = logging.getLogger(__name__)


class VoiceLoop:
    """Reference implementation of the streaming voice loop."""

    def __init__(
        self,
        orchestrator: Orchestrator,
        *,
        playback_callback: Callable[[bytes, Persona], None],
        stt_engine: Optional[STTEngine] = None,
        tts_engine: Optional[TTSEngine] = None,
        default_temp_speaker: str = "mic:default",
    ) -> None:
        self._orchestrator = orchestrator
        self._playback = playback_callback
        self._speaker_temp_id = default_temp_speaker
        self._stt = stt_engine or STTEngine(on_transcript=self._on_transcript)
        self._tts = tts_engine or TTSEngine()

    # ------------------------------------------------------------------
    def start(self) -> None:
        """Start streaming audio ingestion."""

        self._stt.start()

    def stop(self) -> None:
        """Stop streaming audio ingestion."""

        self._stt.stop()

    # ------------------------------------------------------------------
    def push_pcm(self, frame_20ms: bytes, *, speaker_temp_id: Optional[str] = None) -> None:
        """Feed a 20ms PCM frame from ``speaker_temp_id`` into the loop."""

        if speaker_temp_id is not None:
            self._speaker_temp_id = speaker_temp_id
        self._stt.push_audio(frame_20ms)

    # ------------------------------------------------------------------
    def _on_transcript(self, event: TranscriptEvent) -> None:
        text = event.text.strip()
        if not text:
            return
        try:
            response_text, persona = self._orchestrator.process(text, self._speaker_temp_id)
        except Exception:  # pragma: no cover - operational safety net
            _LOGGER.exception("Orchestrator failure while handling transcript")
            return
        try:
            audio = self._tts.synth(persona=persona, text=response_text)
            self._playback(audio, persona)
        except Exception:  # pragma: no cover - playback failures should not crash loop
            _LOGGER.exception("TTS or playback failure")
