"""Download everything the app needs to run offline. Run once on the laptop (needs internet this one time).

    python scripts/fetch_assets.py            # CLIP + transformers.js + ONNX runtime + default Piper voices
    python scripts/fetch_assets.py --voices en_US-ryan-high en_GB-alan-medium

Files land in vendor/ (served to the phone) and models/piper/ (laptop only). Both are gitignored.
"""
import argparse
import sys
from pathlib import Path

import httpx

ROOT = Path(__file__).resolve().parent.parent
VENDOR = ROOT / "vendor"
VOICES = ROOT / "models" / "piper"

TRANSFORMERS_VERSION = "4.3.1"
CLIP = "Xenova/clip-vit-base-patch32"
CLIP_FILES = ["config.json", "preprocessor_config.json", "onnx/vision_model_quantized.onnx"]

# A spread of voices so neighbouring rocks don't sound alike. Browse more: https://rhasspy.github.io/piper-samples/
DEFAULT_VOICES = [
    "en_GB-alan-medium",
    "en_GB-northern_english_male-medium",
    "en_US-ryan-high",
    "en_US-lessac-medium",
    "en_GB-cori-high",
    "en_US-joe-medium",
]

JSDELIVR = "https://cdn.jsdelivr.net/npm"
HF = "https://huggingface.co"


def download(url: str, dest: Path):
    if dest.exists() and dest.stat().st_size > 0:
        print(f"  have {dest.relative_to(ROOT)}")
        return
    dest.parent.mkdir(parents=True, exist_ok=True)
    tmp = dest.with_suffix(dest.suffix + ".part")
    with httpx.stream("GET", url, follow_redirects=True, timeout=60) as r:
        r.raise_for_status()
        total = int(r.headers.get("content-length", 0))
        done = 0
        with open(tmp, "wb") as f:
            for chunk in r.iter_bytes(1 << 20):
                f.write(chunk)
                done += len(chunk)
                if total:
                    print(f"\r  {dest.name}: {done / 1e6:.1f}/{total / 1e6:.1f} MB", end="", flush=True)
    print(f"\r  got {dest.relative_to(ROOT)} ({done / 1e6:.1f} MB)        ")
    tmp.replace(dest)


def voice_url(voice_id: str, ext: str) -> str:
    lang, name, quality = voice_id.split("-")
    return f"{HF}/rhasspy/piper-voices/resolve/main/{lang.split('_')[0]}/{lang}/{name}/{quality}/{voice_id}{ext}"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--voices", nargs="*", default=DEFAULT_VOICES)
    ap.add_argument("--skip-voices", action="store_true")
    args = ap.parse_args()

    print("transformers.js + ONNX Runtime (browser)")
    pkg = httpx.get(f"{JSDELIVR}/@huggingface/transformers@{TRANSFORMERS_VERSION}/package.json", timeout=30).json()
    ort_version = pkg["dependencies"]["onnxruntime-web"]
    download(f"{JSDELIVR}/@huggingface/transformers@{TRANSFORMERS_VERSION}/dist/transformers.min.js",
             VENDOR / "transformers.min.js")
    for f in ("ort-wasm-simd-threaded.mjs", "ort-wasm-simd-threaded.wasm"):
        download(f"{JSDELIVR}/onnxruntime-web@{ort_version}/dist/{f}", VENDOR / "ort" / f)

    print(f"CLIP ({CLIP}, 8-bit)")
    for f in CLIP_FILES:
        download(f"{HF}/{CLIP}/resolve/main/{f}", VENDOR / "models" / CLIP / f)

    if not args.skip_voices:
        print("Piper voices")
        failed = []
        for v in args.voices:
            try:
                download(voice_url(v, ".onnx.json"), VOICES / f"{v}.onnx.json")
                download(voice_url(v, ".onnx"), VOICES / f"{v}.onnx")
            except Exception as e:
                failed.append(v)
                print(f"  ! {v}: {e}")
        if failed:
            print(f"Some voices failed: {failed}. Pick others from https://rhasspy.github.io/piper-samples/")

    print("Done. Everything above is now local.")


if __name__ == "__main__":
    sys.exit(main())
