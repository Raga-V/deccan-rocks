# Deccan Rocks

Adopt a 2.5-billion-year-old granite boulder in Hyderabad. Visit it. It talks to you, slowly and with grudges.

Everything runs on your own devices. The phone works fully offline on the hill. The rock's brain (Gemma) runs on a
laptop at home. No cloud API is involved, and GPS and photos never leave your phone and laptop.

## The design trick: rocks are slow

| In the field (phone, offline) | At home (laptop, local) |
|---|---|
| Snap the rock, read GPS | FastAPI + SQLite |
| CLIP ViT-B/32 in the browser (transformers.js) checks it's the same rock | Gemma 3 via Ollama looks at the photo and invents a personality |
| GPS check: within ~40 m of the adoption spot | Gemma writes the next 3 chapters, a rejection line and guilt-trip nags |
| Plays the next chapter's voice line, cached on the phone | Piper TTS gives every rock its own voice |

A rock you adopt today is **asleep 💤**. Tonight, on home Wi-Fi, you sync and it wakes up. It takes a night to think,
which is reasonable for something older than multicellular life.

```
PHONE (PWA, offline)                      LAPTOP (home, local)
──────────────────────                    ─────────────────────
Camera + GPS capture                      FastAPI server (run.py)
CLIP matcher (transformers.js, WASM)      Ollama → Gemma 3 (vision + text)
Cached voice lines + chapters  ⇄ sync ⇄   Piper TTS → one voice per rock (.wav)
IndexedDB: rocks, visits, embeddings      SQLite: rocks, visits, chapters, lines
```

## Layout

```
run.py                  start the laptop server (HTTPS if certs/ exist)
server/
  main.py               API: /api/sync, /api/wake, /api/health; serves the PWA
  wake.py               the overnight job: wake rocks, top up chapters, render voices
  prompts.py            THE PRODUCT. Iterate here.
  gemma.py, voice.py    Ollama + Piper wrappers
  db.py, config.py
pwa/                    the phone app (vanilla JS, no build step)
  js/app.js             views, matching, sync, walk timer
  js/clip.js            in-browser CLIP embeddings
  sw.js                 offline service worker
scripts/
  fetch_assets.py       one-time download: CLIP, transformers.js, ONNX runtime, Piper voices
  make_cert.py          local HTTPS cert so the phone allows camera/GPS/offline
  try_prompt.py         prompt playground: wake rocks from any photos
  calibration.py        threshold, GPS drift, screen-on stats for the write-up
```

## Setup (Windows laptop)

The project lives on `D:` because `C:` is full. Keep heavy things on `D:` too.

```powershell
cd D:\Gowtami\deccan-rocks
python -m venv .venv
.venv\Scripts\pip install -r server\requirements.txt
.venv\Scripts\python scripts\fetch_assets.py       # ~600 MB, once: CLIP + runtime + 6 Piper voices
```

**Ollama + Gemma, kept on D:**

1. Download `OllamaSetup.exe` from ollama.com and install it to D: with `OllamaSetup.exe /DIR="D:\Ollama"`.
2. Store models on D: by running `setx OLLAMA_MODELS D:\ollama-models`, then quit and restart Ollama from the tray.
3. Pull the model with `ollama pull gemma3:4b`. That's 3.3 GB. On the Quadro T1000 (4 GB) it runs about half on the
   GPU: waking a rock takes ~1.5-2 min, topping up 3 chapters ~40 s.
4. In Ollama's tray Settings, turn off automatic updates if you can: the updater downloads a 1.5 GB installer to
   `C:\Users\<you>\AppData\Local\Ollama\updates_v2` regardless of where Ollama is installed.

Want a smarter rock? `ollama pull gemma3:12b` and `set GEMMA_MODEL=gemma3:12b`. It runs mostly on CPU (32 GB RAM is
fine), so it takes minutes per rock, which is fine for an overnight job.

**Try the prompt before anything else** (Wednesday's job):

```powershell
.venv\Scripts\python scripts\try_prompt.py some-rock.jpg --spot "Khajaguda hill" --human Gowtami --speak
```

Edit `server/prompts.py` until it's genuinely funny. Outputs are saved in `data/prompt-trials/`.

What we learned prompting `gemma3:4b`:

- **It copies examples.** Any sample line in the prompt shows up almost word for word in every rock, so the style
  rules describe the voice instead of quoting it.
- **Make it look before it talks.** The schema asks for `seen` (what's really in the photo) first. Ollama decodes
  fields in schema order, so the chapters build on what it just saw. It still invents the odd plastic bottle.
- **It misfires, so answers are checked.** `prompts.wake_problems` and `continue_problems` reject leaked JSON
  (`], 1,`), repetition loops, chapters that never say "you" (third-person narration), and lines that are too long.
  `gemma.ask_json` retries up to 3 times, and `max_tokens` stops a runaway answer.

## Phone setup (Android + Chrome)

Browsers only allow camera, GPS and offline mode on secure origins. Pick **one** of the two options below, and stick
with it: switching later changes the app's origin, and the phone's rock data won't carry over.

**Important:** reserve your laptop's IP in your router (DHCP reservation). The phone stores rocks per address, so if the
laptop's IP changes, the app looks empty (your data is still on the laptop, and a sync restores it).

**Option A: local HTTPS (recommended, the same setup works for the demo)**

```powershell
.venv\Scripts\python scripts\make_cert.py
.venv\Scripts\python run.py                 # prints https://<laptop-ip>:8443
```

1. On the phone, open `https://<laptop-ip>:8443/rootCA.crt`. Tap through the warning and download the file.
2. Go to Settings → Security & privacy → More security settings → Encryption & credentials → Install a certificate → **CA certificate**, then pick the file.
3. Reopen `https://<laptop-ip>:8443` in Chrome. There should be no warning now. Use ⋮ → **Add to Home screen**.

**Option B: Chrome flag (fastest for a quick test)**

1. Run `.venv\Scripts\python run.py --http`.
2. On the phone, open `chrome://flags/#unsafely-treat-insecure-origin-as-secure`, add `http://<laptop-ip>:8000`, enable it, and relaunch Chrome.

Windows will ask whether to allow Python through the firewall. Allow it on **private** networks.

Then, at home, go to **Home tab → Download for offline**. That caches about 100 MB of CLIP onto the phone. After that,
the app works with Wi-Fi and data switched off.

## The daily loop

1. **Field (offline):** Rocks → *Adopt*. Start a walk on the Home tab. Use *Visit* to photograph a rock. The best CLIP match within the GPS radius has to clear the threshold. If it does, the next chapter plays. If it doesn't, the rock that *should* be there objects.
2. **Home:** Home → *Wake the rocks* syncs first, then runs Gemma and Piper on the laptop and syncs again. You can also run `.venv\Scripts\python -m server.wake` on the laptop, or add `--mock` to test the whole loop without Gemma.
3. **Calibrate:** Home → *Calibration photo* records the similarity to every rock along with which rock it really was. At home, run `.venv\Scripts\python scripts\calibration.py` to get the suggested threshold, the confusable pairs, the GPS drift, and screen-on seconds per walk.

To re-roll a rock's personality while you're tuning the prompt, run `python -m server.wake --rewake <id-prefix>`.

## What's actually local

| | Where it runs | Network |
|---|---|---|
| Rock matching (CLIP) | phone browser | none, since the model is cached on the phone |
| GPS check | phone | none |
| Personality and chapters (Gemma 3) | laptop, Ollama | none |
| Voices (Piper) | laptop, CPU | none |
| Sync | phone ⇄ laptop | home Wi-Fi only |
| One-time setup | `fetch_assets.py`, `ollama pull` | downloads open weights from Hugging Face, jsDelivr and Ollama |

The laptop server has no auth. Only run it on your home network.
