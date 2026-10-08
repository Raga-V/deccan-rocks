"""Piper TTS: every rock gets its own voice. CPU-only, offline."""
import hashlib
import re
import wave
from functools import lru_cache
from pathlib import Path

from .config import VOICES_DIR

try:
    from piper import PiperVoice, SynthesisConfig
except ImportError:  # TTS is optional; the app falls back to text-only chapters
    PiperVoice = None


def installed_voices() -> list[str]:
    """Voice ids like 'en_GB-alan-medium' for which both .onnx and .onnx.json exist."""
    if not VOICES_DIR.exists():
        return []
    return sorted(
        p.stem for p in VOICES_DIR.glob("*.onnx") if p.with_suffix(".onnx.json").exists()
    )


def available() -> bool:
    return PiperVoice is not None and bool(installed_voices())


@lru_cache(maxsize=8)
def _load(voice_id: str):
    return PiperVoice.load(str(VOICES_DIR / f"{voice_id}.onnx"))


def speed_for(rock_id: str) -> float:
    """Old rocks speak slowly. Deterministic per rock: length_scale between 1.05 and 1.35."""
    h = int(hashlib.sha1(rock_id.encode()).hexdigest()[:8], 16)
    return round(1.05 + (h % 31) / 100, 2)


def clean_for_speech(text: str) -> str:
    text = re.sub(r"\*[^*]*\*|\[[^\]]*\]", " ", text)  # stage directions: *sighs*, [pause]
    text = re.sub(r"[*_#`~{}<>]", "", text)
    text = re.sub(r"[\U0001F000-\U0001FFFF☀-➿]", "", text)  # emoji
    return re.sub(r"\s+", " ", text).strip().strip('"')


def speak(text: str, voice_id: str, out_path: Path, length_scale: float = 1.15) -> Path | None:
    """Render text to a WAV file. Returns None if TTS isn't set up."""
    if PiperVoice is None or voice_id not in installed_voices():
        return None
    out_path.parent.mkdir(parents=True, exist_ok=True)
    cfg = SynthesisConfig(length_scale=length_scale, noise_scale=0.6, noise_w_scale=0.7)
    with wave.open(str(out_path), "wb") as wf:
        _load(voice_id).synthesize_wav(clean_for_speech(text), wf, syn_config=cfg)
    return out_path
