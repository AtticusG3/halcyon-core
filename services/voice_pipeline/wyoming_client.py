"""Wyoming protocol client for TTS output."""
from __future__ import annotations

import asyncio
import base64
import io
import json
import logging
import struct
import wave
from typing import Optional

try:
    import websockets
except ImportError:  # pragma: no cover
    websockets = None

_LOGGER = logging.getLogger(__name__)


class WyomingClientError(RuntimeError):
    """Raised when Wyoming client operations fail."""


class WyomingClient:
    """WebSocket client for Wyoming TTS protocol."""

    def __init__(self, host: str, port: int, *, timeout: float = 5.0) -> None:
        """Initialize the Wyoming client.

        Parameters
        ----------
        host:
            Wyoming server hostname or IP.
        port:
            Wyoming server port.
        timeout:
            Connection timeout in seconds.
        """
        if websockets is None:
            raise WyomingClientError("websockets package is required. Install with: pip install websockets")

        self._host = host
        self._port = port
        self._timeout = timeout
        self._url = f"ws://{host}:{port}"

    async def send_tts(self, wav_bytes: bytes) -> bool:
        """Send TTS audio to Wyoming server.

        Parameters
        ----------
        wav_bytes:
            WAV audio bytes to send.

        Returns
        -------
        True if successful, False otherwise.
        """
        try:
            async with websockets.connect(self._url) as websocket:
                # Wyoming protocol: send TTS request
                # Format: JSON message with type and audio data
                message = {
                    "type": "tts",
                    "audio": base64.b64encode(wav_bytes).decode("utf-8"),
                }
                await asyncio.wait_for(websocket.send(json.dumps(message)), timeout=self._timeout)

                # Wait for acknowledgment (Wyoming may send a response)
                try:
                    response = await asyncio.wait_for(websocket.recv(), timeout=2.0)
                    _LOGGER.debug("Wyoming TTS response: %s", response[:100])
                except asyncio.TimeoutError:
                    # No response is OK - some Wyoming servers don't send one
                    pass

                return True
        except Exception as exc:
            _LOGGER.warning("Wyoming TTS send failed: %s", exc)
            return False

    def send_tts_sync(self, wav_bytes: bytes) -> bool:
        """Synchronous wrapper for send_tts.

        Parameters
        ----------
        wav_bytes:
            WAV audio bytes to send.

        Returns
        -------
        True if successful, False otherwise.
        """
        try:
            loop = asyncio.get_event_loop()
        except RuntimeError:
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)

        if loop.is_running():
            # If loop is already running, create a task
            # This is a simplified approach - in production, you might want
            # to use a dedicated thread or async context
            _LOGGER.warning("Event loop is running; using run_until_complete (may block)")
            return loop.run_until_complete(self.send_tts(wav_bytes))
        else:
            return loop.run_until_complete(self.send_tts(wav_bytes))

    @staticmethod
    def create_chime_wav(duration_ms: int = 200, frequency: int = 800) -> bytes:
        """Create a simple chime WAV file.

        Parameters
        ----------
        duration_ms:
            Duration in milliseconds.
        frequency:
            Frequency in Hz.

        Returns
        -------
        WAV bytes.
        """
        sample_rate = 16000
        duration = duration_ms / 1000.0
        num_samples = int(sample_rate * duration)
        wav_buffer = io.BytesIO()

        with wave.open(wav_buffer, "wb") as wf:
            wf.setnchannels(1)  # Mono
            wf.setsampwidth(2)  # 16-bit
            wf.setframerate(sample_rate)

            # Generate a simple tone (sine wave with fade)
            import math

            frames = []
            for i in range(num_samples):
                t = i / sample_rate
                # Sine wave with fade in/out
                fade = 1.0
                if i < num_samples * 0.1:
                    fade = i / (num_samples * 0.1)
                elif i > num_samples * 0.9:
                    fade = (num_samples - i) / (num_samples * 0.1)
                amplitude = int(32767 * 0.3 * fade * math.sin(2 * math.pi * frequency * t))
                frames.append(struct.pack("<h", amplitude))

            wf.writeframes(b"".join(frames))

        wav_buffer.seek(0)
        return wav_buffer.read()


__all__ = ["WyomingClient", "WyomingClientError"]

