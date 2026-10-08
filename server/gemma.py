"""Talk to Gemma through the local Ollama server. No cloud."""
import base64
import io
import json
from pathlib import Path

import httpx
from PIL import Image

from .config import GEMMA_MODEL, OLLAMA_TIMEOUT_S, OLLAMA_URL


class GemmaUnavailable(RuntimeError):
    pass


def available() -> bool:
    try:
        r = httpx.get(f"{OLLAMA_URL}/api/tags", timeout=3)
        names = {m["name"] for m in r.json().get("models", [])}
        return GEMMA_MODEL in names or f"{GEMMA_MODEL}:latest" in names
    except Exception:
        return False


def _image_b64(path: Path, max_side: int = 896) -> str:
    # Gemma 3's vision encoder works at 896x896; sending bigger photos only costs time.
    img = Image.open(path).convert("RGB")
    img.thumbnail((max_side, max_side))
    buf = io.BytesIO()
    img.save(buf, format="JPEG", quality=88)
    return base64.b64encode(buf.getvalue()).decode()


def ask_json(prompt: str, schema: dict, image: Path | None = None, temperature: float = 1.0) -> dict:
    msg = {"role": "user", "content": prompt}
    if image is not None:
        msg["images"] = [_image_b64(image)]
    body = {
        "model": GEMMA_MODEL,
        "messages": [msg],
        "format": schema,  # Ollama structured output: constrains decoding to this JSON schema
        "stream": False,
        "options": {"temperature": temperature, "top_p": 0.95, "top_k": 64, "num_ctx": 8192},
    }
    try:
        r = httpx.post(f"{OLLAMA_URL}/api/chat", json=body, timeout=OLLAMA_TIMEOUT_S)
    except httpx.ConnectError as e:
        raise GemmaUnavailable(f"Ollama not reachable at {OLLAMA_URL} ({e}). Is `ollama serve` running?") from e
    if r.status_code == 404:
        raise GemmaUnavailable(f"Model {GEMMA_MODEL} not found. Run: ollama pull {GEMMA_MODEL}")
    r.raise_for_status()
    return json.loads(r.json()["message"]["content"])
