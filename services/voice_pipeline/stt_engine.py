"""Streaming speech-to-text engine leveraging faster-whisper and WebRTC VAD."""
from __future__ import annotations

import io
import queue
import threading
import time
import wave
from dataclasses import dataclass
from typing import Callable, List, Optional

try:  # pragma: no cover - import guard
    import webrtcvad  # type: ignore
except Exception:  # pragma: no cover - optional dependency
    webrtcvad = None  # type: ignore

try:  # pragma: no cover - import guard
    from faster_whisper import WhisperModel  # type: ignore
except Exception:  # pragma: no cover - optional dependency
    WhisperModel = None  # type: ignore

PCM_RATE = 16_000
PCM_WIDTH = 2  # 16-bit audio
PCM_CHANNELS = 1
FRAME_DURATION_MS = 20
SAMPLES_PER_FRAME = int(PCM_RATE * (FRAME_DURATION_MS / 1000.0))
FRAME_SIZE_BYTES = SAMPLES_PER_FRAME * PCM_WIDTH


class STTDependencyError(RuntimeError):
    """Raised when the STT engine cannot initialise due to missing dependencies."""


@dataclass
class TranscriptEvent:
    """Represents a completed transcription event."""

    text: str
    duration: float
    start_time: float


class STTEngine:
    """Streaming STT pipeline with WebRTC VAD front-end and faster-whisper backend."""

    def __init__(
        self,
        *,
        model_path: str = "medium.en",
        device: str = "cuda",
        compute_type: str = "float16",
        vad_aggressiveness: int = 2,
        max_utterance_sec: float = 12.0,
        on_transcript: Optional[Callable[[TranscriptEvent], None]] = None,
    ) -> None:
        if WhisperModel is None or webrtcvad is None:
            raise STTDependencyError(
                "faster-whisper and webrtcvad must be installed to use STTEngine. "
                "Install them with `pip install faster-whisper webrtcvad`."
            )
        self._model = WhisperModel(model_path, device=device, compute_type=compute_type)
        self._vad = webrtcvad.Vad(vad_aggressiveness)
        self._max_frames = int(max_utterance_sec * 1000 / FRAME_DURATION_MS)
        self._callback = on_transcript
        self._queue: "queue.Queue[bytes]" = queue.Queue(maxsize=4096)
        self._thread = threading.Thread(target=self._loop, daemon=True)
        self._stop = threading.Event()
        self._current_start: Optional[float] = None

    # ------------------------------------------------------------------
    def start(self) -> None:
        """Start the background processing thread."""

        if self._thread.is_alive():
            return
        self._stop.clear()
        self._thread = threading.Thread(target=self._loop, daemon=True)
        self._thread.start()

    def stop(self, timeout: float = 2.0) -> None:
        """Stop the background processing thread."""

        self._stop.set()
        if self._thread.is_alive():
            self._thread.join(timeout=timeout)

    # ------------------------------------------------------------------
    def push_audio(self, pcm_20ms: bytes) -> None:
        """Feed a single 20 ms PCM frame into the recogniser."""

        if len(pcm_20ms) != FRAME_SIZE_BYTES:
            return  # drop malformed frames silently to keep the stream healthy
        try:
            self._queue.put_nowait(pcm_20ms)
        except queue.Full:  # pragma: no cover - defensive backpressure
            try:
                _ = self._queue.get_nowait()
            except queue.Empty:
                pass
            try:
                self._queue.put_nowait(pcm_20ms)
            except queue.Full:
                pass

    # ------------------------------------------------------------------
    def _loop(self) -> None:
        buffer: List[bytes] = []
        silence_tail = 0
        self._current_start = None
        while not self._stop.is_set():
            try:
                frame = self._queue.get(timeout=0.1)
            except queue.Empty:
                if buffer and silence_tail >= 8:
                    self._flush(buffer)
                    buffer.clear()
                    silence_tail = 0
                    self._current_start = None
                continue

            if self._current_start is None:
                self._current_start = time.time()

            is_speech = False
            try:
                is_speech = bool(self._vad.is_speech(frame, PCM_RATE))
            except Exception:  # pragma: no cover - defensive
                is_speech = False

            buffer.append(frame)
            if is_speech:
                silence_tail = 0
            else:
                silence_tail += 1

            if silence_tail >= 12 or len(buffer) >= self._max_frames:
                self._flush(buffer)
                buffer.clear()
                silence_tail = 0
                self._current_start = None

    # ------------------------------------------------------------------
    def _flush(self, frames: List[bytes]) -> None:
        if not frames:
            return
        start_time = self._current_start or time.time()
        duration = len(frames) * FRAME_DURATION_MS / 1000.0
        wav = io.BytesIO()
        with wave.open(wav, "wb") as wf:
            wf.setnchannels(PCM_CHANNELS)
            wf.setsampwidth(PCM_WIDTH)
            wf.setframerate(PCM_RATE)
            wf.writeframes(b"".join(frames))
        wav.seek(0)

        segments, info = self._model.transcribe(
            wav,
            language="en",
            vad_filter=False,
            beam_size=1,
        )
        text = "".join(segment.text for segment in segments).strip()
        if text and self._callback:
            event = TranscriptEvent(text=text, duration=duration, start_time=start_time)
            try:
                self._callback(event)
            except Exception:  # pragma: no cover - callbacks must not break the loop
                pass
