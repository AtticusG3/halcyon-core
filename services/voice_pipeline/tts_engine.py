"""Persona-aware text-to-speech helpers."""
from __future__ import annotations

import io
import os
import subprocess
import tempfile
import wave
from typing import Dict, Literal, Optional

try:  # pragma: no cover - optional dependency
    import requests  # type: ignore
except Exception:  # pragma: no cover - optional dependency
    requests = None  # type: ignore

Persona = Literal["HALSTON", "SCARLET"]


class TTSDependencyError(RuntimeError):
    """Raised when required TTS dependencies are missing."""


class TTSEngine:
    """Persona-aware TTS pipeline supporting XTTS HTTP and Piper CLI backends."""

    def __init__(
        self,
        *,
        backend: Literal["xtts_http", "piper_cmd"] = "xtts_http",
        xtts_url: str = "http://127.0.0.1:8020/api/tts",
        halston_ref: Optional[str] = None,
        scarlet_ref: Optional[str] = None,
        piper_voice_halston: str = "en_GB-cori-high",
        piper_voice_scarlet: str = "en_US-amy-low",
        timeout: float = 30.0,
    ) -> None:
        self.backend = backend
        self.xtts_url = xtts_url
        self.halston_ref = halston_ref
        self.scarlet_ref = scarlet_ref
        self.piper_voice_halston = piper_voice_halston
        self.piper_voice_scarlet = piper_voice_scarlet
        self.timeout = timeout
        if backend == "xtts_http" and requests is None:
            raise TTSDependencyError(
                "The requests package is required for the XTTS HTTP backend. "
                "Install it with `pip install requests` or choose backend='piper_cmd'."
            )

    # ------------------------------------------------------------------
    def synth(self, persona: Persona, text: str) -> bytes:
        """Synthesize speech for ``text`` using the selected persona."""

        if self.backend == "xtts_http":
            return self._synth_xtts(persona, text)
        return self._synth_piper(persona, text)

    # ------------------------------------------------------------------
    def _synth_xtts(self, persona: Persona, text: str) -> bytes:
        assert requests is not None  # checked in __init__
        files: Dict[str, object] = {}
        data: Dict[str, object] = {"text": text, "language": "en"}
        ref_path = self.halston_ref if persona == "HALSTON" else self.scarlet_ref
        if ref_path:
            files["speaker_wav"] = open(ref_path, "rb")
        else:
            data["speaker"] = "halston" if persona == "HALSTON" else "scarlet"
        try:
            response = requests.post(self.xtts_url, data=data, files=files or None, timeout=self.timeout)
            response.raise_for_status()
            return response.content
        except Exception:
            return self._fallback_tone(persona, text)
        finally:
            for fh in files.values():
                try:
                    fh.close()
                except Exception:  # pragma: no cover - defensive
                    pass

    def _synth_piper(self, persona: Persona, text: str) -> bytes:
        voice = self.piper_voice_halston if persona == "HALSTON" else self.piper_voice_scarlet
        with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as tmp:
            tmp_path = tmp.name
        try:
            cmd = [
                "piper",
                "--model",
                voice,
                "--output_file",
                tmp_path,
            ]
            proc = subprocess.Popen(cmd, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
            _, stderr = proc.communicate(input=text.encode("utf-8"), timeout=self.timeout)
            if proc.returncode != 0:
                raise RuntimeError(f"Piper exited with status {proc.returncode}: {stderr.decode('utf-8', 'ignore')}")
            with open(tmp_path, "rb") as fh:
                return fh.read()
        except Exception:
            return self._fallback_tone(persona, text)
        finally:
            try:
                os.remove(tmp_path)
            except OSError:  # pragma: no cover - defensive
                pass

    # ------------------------------------------------------------------
    def _fallback_tone(self, persona: Persona, text: str) -> bytes:
        """Return a short silence WAV with an embedded marker in case of failure."""

        duration_seconds = 0.2
        num_frames = int(PCM_RATE * duration_seconds)
        silence_frame = (0).to_bytes(2, byteorder="little", signed=True)
        audio = silence_frame * num_frames
        wav_buffer = io.BytesIO()
        with wave.open(wav_buffer, "wb") as wf:
            wf.setnchannels(1)
            wf.setsampwidth(2)
            wf.setframerate(PCM_RATE)
            wf.writeframes(audio)
        wav_buffer.seek(0)
        data = wav_buffer.read()
        marker = f"[{persona} TTS unavailable] {text}".encode("utf-8")
        return data + b"\n" + marker


# Constants reused from the STT module to ensure consistent audio parameters
PCM_RATE = 16_000
