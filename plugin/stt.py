"""Local Whisper transcription (faster-whisper, CPU int8, no cloud)."""
from __future__ import annotations

import io
import logging
import threading
from typing import Optional

logger = logging.getLogger(__name__)


class Transcriber:
    """Lazily-loaded faster-whisper model. Accepts opus/webm, m4a/aac, wav bytes."""

    def __init__(self, model_size: str = "base") -> None:
        self.model_size = model_size or "base"
        self._model = None
        self._lock = threading.Lock()

    def _get_model(self):
        with self._lock:
            if self._model is None:
                from faster_whisper import WhisperModel
                logger.info("tether: loading whisper '%s' (first run downloads the model)", self.model_size)
                self._model = WhisperModel(self.model_size, device="cpu", compute_type="int8")
                logger.info("tether: whisper '%s' ready", self.model_size)
            return self._model

    def transcribe(self, audio: bytes) -> str:
        if not audio:
            return ""
        model = self._get_model()
        segments, _info = model.transcribe(
            io.BytesIO(audio),
            beam_size=1,
            vad_filter=True,
            vad_parameters=dict(min_silence_duration_ms=400),
        )
        return " ".join(s.text.strip() for s in segments).strip()
