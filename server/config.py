"""Paths and knobs. Everything is local; override with env vars if needed."""
import os
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

DATA_DIR = Path(os.getenv("ROCKS_DATA", ROOT / "data"))
MEDIA_DIR = DATA_DIR / "media"          # photos + generated audio
DB_PATH = DATA_DIR / "rocks.db"

PWA_DIR = ROOT / "pwa"                  # the phone app (static files)
VENDOR_DIR = ROOT / "vendor"            # transformers.js, onnxruntime wasm, CLIP weights (served to phone)
VOICES_DIR = ROOT / "models" / "piper"  # Piper voices (laptop only)
CERTS_DIR = ROOT / "certs"

OLLAMA_URL = os.getenv("OLLAMA_URL", "http://127.0.0.1:11434")
GEMMA_MODEL = os.getenv("GEMMA_MODEL", "gemma3:4b")
OLLAMA_TIMEOUT_S = float(os.getenv("OLLAMA_TIMEOUT_S", "900"))  # 12B on CPU is slow; rocks are patient
# Smaller context = more of the model fits on a 4 GB GPU. Prompts + 20 chapters of history stay well under 4k.
OLLAMA_NUM_CTX = int(os.getenv("OLLAMA_NUM_CTX", "4096"))

# Generate this many chapters at a time, and top up when fewer than CHAPTER_LOW_WATER remain locked.
CHAPTER_BATCH = 3
CHAPTER_LOW_WATER = 2

# The overnight wake runs by itself at this local time while the server is up ("off" to disable).
NIGHTLY_WAKE = os.getenv("NIGHTLY_WAKE", "02:00")

# Guilt-trip lines, pre-generated at wake time so the phone can show them offline.
# (days without a visit, tier name). Keep in sync with NAG_TIERS in pwa/app.js.
NAG_TIERS = [(3, "mild"), (7, "sulky"), (14, "dramatic")]

for d in (DATA_DIR, MEDIA_DIR):
    d.mkdir(parents=True, exist_ok=True)
