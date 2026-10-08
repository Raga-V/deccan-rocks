"""Prompt playground: wake up rocks from any photos, no phone or database involved.

    python scripts/try_prompt.py photos/*.jpg
    python scripts/try_prompt.py rock.jpg --spot "Khajaguda hill" --human Gowtami --speak

Iterate on server/prompts.py until the output is genuinely funny. Prompt quality is the product.
Results go to data/prompt-trials/ so you can compare runs.
"""
import argparse
import datetime as dt
import glob
import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from server import gemma, prompts, voice  # noqa: E402
from server.config import DATA_DIR, GEMMA_MODEL  # noqa: E402

ap = argparse.ArgumentParser()
ap.add_argument("photos", nargs="+")
ap.add_argument("--spot")
ap.add_argument("--human")
ap.add_argument("--speak", action="store_true", help="also render chapter 1 with each installed Piper voice")
args = ap.parse_args()
sys.stdout.reconfigure(encoding="utf-8")  # Gemma likes curly quotes; the Windows console doesn't

out_dir = DATA_DIR / "prompt-trials" / dt.datetime.now().strftime("%Y%m%d-%H%M%S")
out_dir.mkdir(parents=True, exist_ok=True)
paths = [Path(p) for pat in args.photos for p in (glob.glob(pat) or [pat])]

for path in paths:
    print(f"\n=== {path.name} ({GEMMA_MODEL}) ===")
    t0 = time.time()
    try:
        data = gemma.ask_json(prompts.wake_prompt(args.spot, args.human, None), prompts.WAKE_SCHEMA, image=path,
                              check=prompts.wake_problems)
    except gemma.GemmaBadOutput as e:
        print(f"[{time.time() - t0:.0f}s] gave up: {e}")
        continue
    print(f"[{time.time() - t0:.0f}s]")
    print(f"{data['name']}: {data['temperament']}")
    print(f"  seen:        {'; '.join(data['seen'])}")
    print(f"  grudge:      {data['grudge']}")
    print(f"  secret like: {data['secret_like']}")
    print(f"  quirks:      {'; '.join(data['quirks'])}")
    print(f"  backstory:   {data['backstory']}")
    for i, ch in enumerate(data["chapters"], 1):
        print(f"  ch{i} ({len(ch.split())}w): {ch}")
    print(f"  rejection:   {data['rejection_line']}")
    for tier, line in data["nags"].items():
        print(f"  nag/{tier}: {line}")
    (out_dir / f"{path.stem}.json").write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")
    if args.speak:
        for v in voice.installed_voices():
            voice.speak(data["chapters"][0], v, out_dir / f"{path.stem}-{v}.wav")

print(f"\nSaved to {out_dir}")
