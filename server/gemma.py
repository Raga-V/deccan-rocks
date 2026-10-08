"""Talk to Gemma through the local Ollama server. No cloud."""
import base64
import io
import json
from collections.abc import Callable
from pathlib import Path

import httpx
from PIL import Image

from .config import GEMMA_MODEL, OLLAMA_NUM_CTX, OLLAMA_TIMEOUT_S, OLLAMA_URL


class GemmaUnavailable(RuntimeError):
    pass


class GemmaBadOutput(RuntimeError):
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


def ask_json(prompt: str, schema: dict, image: Path | None = None, temperature: float = 1.0,
             check: Callable[[dict], list[str]] | None = None, tries: int = 3, max_tokens: int = 1500) -> dict:
    """Ask Gemma for JSON. If `check` finds problems with the answer, ask again (small models misfire sometimes).

    max_tokens caps a runaway answer: a 4B model can loop inside a JSON string for thousands of tokens."""
    for attempt in range(1, tries + 1):
        try:
            data = _ask_json_once(prompt, schema, image, temperature, max_tokens)
            problems = check(data) if check else []
        except json.JSONDecodeError:
            problems = [f"answer cut off at {max_tokens} tokens (rambling)"]
        except httpx.TimeoutException:
            problems = [f"no answer within {OLLAMA_TIMEOUT_S:.0f}s"]
        if not problems:
            return data
        print(f"  gemma attempt {attempt}/{tries} rejected: {'; '.join(problems)}", flush=True)
    raise GemmaBadOutput(f"no usable answer after {tries} tries: {'; '.join(problems)}")


def _ask_json_once(prompt: str, schema: dict, image: Path | None, temperature: float, max_tokens: int) -> dict:
    msg = {"role": "user", "content": prompt}
    if image is not None:
        msg["images"] = [_image_b64(image)]
    body = {
        "model": GEMMA_MODEL,
        "messages": [msg],
        "format": schema,  # Ollama structured output: constrains decoding to this JSON schema
        "stream": False,
        "options": {"temperature": temperature, "top_p": 0.95, "top_k": 64, "num_ctx": OLLAMA_NUM_CTX,
                    "num_predict": max_tokens},
    }
    try:
        r = httpx.post(f"{OLLAMA_URL}/api/chat", json=body, timeout=OLLAMA_TIMEOUT_S)
    except httpx.ConnectError as e:
        raise GemmaUnavailable(f"Ollama not reachable at {OLLAMA_URL} ({e}). Is `ollama serve` running?") from e
    if r.status_code == 404:
        raise GemmaUnavailable(f"Model {GEMMA_MODEL} not found. Run: ollama pull {GEMMA_MODEL}")
    r.raise_for_status()
    return json.loads(r.json()["message"]["content"])
