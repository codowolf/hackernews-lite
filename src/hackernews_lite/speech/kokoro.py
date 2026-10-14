"""Kokoro, an open-source voice model that runs on this Mac for free (ONNX build)."""

from pathlib import Path
from typing import Any

import httpx2 as httpx
import lameenc
import numpy as np
from kokoro_onnx import Kokoro

from hackernews_lite.domain.errors import SpeechError

_RELEASE = "https://github.com/thewh1teagle/kokoro-onnx/releases/download/model-files-v1.0"
MODEL_FILE = "kokoro-v1.0.onnx"
VOICES_FILE = "voices-v1.0.bin"
VOICES = ["af_heart", "af_bella", "af_nicole", "am_adam", "bf_emma", "bm_george"]


class KokoroTts:
    name = "kokoro"

    def __init__(self, models_dir: Path) -> None:
        self._models_dir = models_dir
        self._engine: Any = None

    def voices(self) -> list[str]:
        return VOICES

    def synthesize(self, text: str, voice: str) -> bytes:
        samples, sample_rate = self._load().create(text, voice=voice, speed=1.0, lang="en-us")
        return _to_mp3(samples, sample_rate)

    def cost_usd(self, characters: int) -> float:
        return 0.0

    def _load(self) -> Any:
        if self._engine is None:
            self._models_dir.mkdir(parents=True, exist_ok=True)
            for filename in (MODEL_FILE, VOICES_FILE):
                _download(f"{_RELEASE}/{filename}", self._models_dir / filename)
            self._engine = Kokoro(
                str(self._models_dir / MODEL_FILE), str(self._models_dir / VOICES_FILE)
            )
        return self._engine


def _download(url: str, destination: Path) -> None:
    if destination.exists():
        return
    temp = destination.with_suffix(destination.suffix + ".part")
    try:
        with httpx.stream("GET", url, follow_redirects=True, timeout=120.0) as response:
            response.raise_for_status()
            with temp.open("wb") as handle:
                for block in response.iter_bytes():
                    handle.write(block)
    except httpx.HTTPError as exc:
        raise SpeechError(f"Could not download {url}: {exc}") from exc
    temp.replace(destination)


def _to_mp3(samples: "np.ndarray[Any, Any]", sample_rate: int) -> bytes:
    pcm = (np.clip(samples, -1.0, 1.0) * 32767).astype(np.int16)
    encoder = lameenc.Encoder()
    encoder.set_bit_rate(96)
    encoder.set_in_sample_rate(sample_rate)
    encoder.set_channels(1)
    encoder.set_quality(2)
    return bytes(encoder.encode(pcm.tobytes()) + encoder.flush())
