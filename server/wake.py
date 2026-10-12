"""The overnight wake-up job: rocks are slow, they take a night to think.

    python -m server.wake            # wake sleeping rocks, top up chapters, render missing audio
    python -m server.wake --mock     # same, with canned text (no Ollama needed) to test the loop
    python -m server.wake --rewake ID   # throw away a rock's personality and dream it up again

The phone can also trigger this over Wi-Fi (POST /api/wake).
"""
import argparse
import datetime as dt
import hashlib
import json
import random
import threading
import time

from . import db, gemma, prompts, voice
from .config import CHAPTER_BATCH, CHAPTER_LOW_WATER, GEMMA_MODEL, MEDIA_DIR, MOCK_BRAIN, NAG_TIERS

_lock = threading.Lock()
status = {"running": False, "started_at": None, "finished_at": None, "log": [], "sleeping": 0}
_failed_at: dict[str, float] = {}  # rock id -> when Gemma last gave up on it
RETRY_AFTER_S = 30 * 60  # the phone syncs every 20 s; don't hammer a rock Gemma keeps failing on


def log(msg: str):
    line = f"{dt.datetime.now():%H:%M:%S} {msg}"
    print(line, flush=True)
    status["log"] = (status["log"] + [line])[-200:]


def now() -> str:
    return dt.datetime.now().astimezone().isoformat(timespec="seconds")


def _when(iso: str) -> str:
    return dt.datetime.fromisoformat(iso).astimezone().strftime("%a %d %b, %I:%M %p")  # phone sends UTC; show laptop-local


# ---------------------------------------------------------------- voices + audio

def pick_voice(c, rock_id: str) -> str | None:
    """Least-used installed voice, so neighbouring rocks don't sound alike."""
    voices = voice.installed_voices()
    if not voices:
        return None
    used = {v: 0 for v in voices}
    for (v,) in c.execute("SELECT piper_voice FROM rocks WHERE piper_voice IS NOT NULL AND id != ?", (rock_id,)):
        if v in used:
            used[v] += 1
    return min(voices, key=lambda v: (used[v], hashlib.md5(f"{rock_id}{v}".encode()).hexdigest()))


def render(rock, text: str, stem: str) -> str | None:
    if not rock["piper_voice"]:
        return None
    out = MEDIA_DIR / rock["id"] / f"{stem}-{int(time.time())}.wav"
    path = voice.speak(text, rock["piper_voice"], out, rock["voice_speed"] or 1.15)
    return path.relative_to(MEDIA_DIR).as_posix() if path else None


# ---------------------------------------------------------------- mock brain (for testing without Gemma)

def _mock_wake(rock) -> dict:
    rnd = random.Random(rock["id"])
    name = rnd.choice(["Gundu", "Old Sitamma", "Pebble Rao", "Big Lakshmi", "Bandaraju"])
    return {
        "seen": ["a balanced boulder", "a hazy sky", "a crane"],
        "name": name, "temperament": "suspicious, sleepy", "quirks": ["counts pigeons", "hates drills"],
        "grudge": "the new tower that blocks the sunset", "secret_like": "the smell of Irani chai",
        "neighbour": "Venkat", "backstory": f"{name} has been sitting here since before the continents moved. (mock)",
        "chapters": [f"[mock] Chapter {i}. Hello again. I am {name}. I have not moved. Have you?" for i in (1, 2, 3)],
        "rejection_line": "[mock] That is Venkat. I am NOT Venkat.",
        "nags": {t: f"[mock] {d} days. A pigeon sat on me. I let it." for d, t in NAG_TIERS},
    }


def _mock_continue(n: int, first: int) -> dict:
    return {"chapters": [f"[mock] Chapter {first + i}. You came back. Suspicious." for i in range(n)]}


# ---------------------------------------------------------------- jobs

def wake_rock(rock_id: str, mock: bool):
    with db.tx() as c:
        rock = c.execute("SELECT * FROM rocks WHERE id=?", (rock_id,)).fetchone()
        human = db.get_setting(c, "human_name")
        voice_id = pick_voice(c, rock_id)
        taken = [r["name"] for r in c.execute("SELECT name FROM rocks WHERE name IS NOT NULL AND id != ?", (rock_id,))]
    log(f"waking {rock_id[:8]} ({rock['spot'] or 'no spot label'}) with {'mock' if mock else GEMMA_MODEL}...")
    t0 = time.time()
    if mock:
        data = _mock_wake(rock)
    else:
        data = gemma.ask_json(
            prompts.wake_prompt(rock["spot"], human, _when(rock["adopted_at"]), taken),
            prompts.WAKE_SCHEMA,
            image=MEDIA_DIR / rock["photo_path"],
            check=prompts.wake_problems,
        )
    log(f"  {data['name']} woke up in {time.time() - t0:.0f}s: {data['temperament']}")

    rock = dict(rock, piper_voice=voice_id, voice_speed=voice.speed_for(rock_id))
    chapters = [(i + 1, t, render(rock, t, f"ch{i + 1}")) for i, t in enumerate(data["chapters"][:CHAPTER_BATCH])]
    rejection = data["rejection_line"]
    nags = [(tier, days, data["nags"][tier]) for days, tier in NAG_TIERS]
    rej_audio = render(rock, rejection, "rejection")
    nag_audio = [render(rock, text, f"nag-{tier}") for tier, _, text in nags]

    personality = {k: data[k] for k in ("seen", "temperament", "quirks", "grudge", "secret_like", "neighbour")}
    ts = now()
    with db.tx() as c:
        c.execute(
            "UPDATE rocks SET status='awake', name=?, personality=?, backstory=?, piper_voice=?, voice_speed=?, woke_at=? WHERE id=?",
            (data["name"], json.dumps(personality), data["backstory"], rock["piper_voice"], rock["voice_speed"], ts, rock_id),
        )
        c.executemany(
            "INSERT OR REPLACE INTO chapters(rock_id, number, text, audio_path, created_at) VALUES(?,?,?,?,?)",
            [(rock_id, n, t, a, ts) for n, t, a in chapters],
        )
        c.execute(
            "INSERT OR REPLACE INTO lines(rock_id, kind, tier, days, text, audio_path, created_at) VALUES(?, 'rejection', '', NULL, ?, ?, ?)",
            (rock_id, rejection, rej_audio, ts),
        )
        c.executemany(
            "INSERT OR REPLACE INTO lines(rock_id, kind, tier, days, text, audio_path, created_at) VALUES(?, 'nag', ?, ?, ?, ?, ?)",
            [(rock_id, tier, days, text, a, ts) for (tier, days, text), a in zip(nags, nag_audio)],
        )
    log(f"  {data['name']} is awake.")


def top_up_rock(rock_id: str, mock: bool):
    """Write the next few chapters once the human is close to running out."""
    with db.tx() as c:
        rock = c.execute("SELECT * FROM rocks WHERE id=?", (rock_id,)).fetchone()
        chapters = c.execute("SELECT * FROM chapters WHERE rock_id=? ORDER BY number", (rock_id,)).fetchall()
        visits = c.execute("SELECT * FROM visits WHERE rock_id=? ORDER BY ts", (rock_id,)).fetchall()
    locked = sum(1 for ch in chapters if not ch["unlocked_at"])
    if locked >= CHAPTER_LOW_WATER:
        return
    first = (chapters[-1]["number"] if chapters else 0) + 1
    personality = json.loads(rock["personality"] or "{}")

    notes, prev = [f"They adopted you on {_when(rock['adopted_at'])}."], dt.datetime.fromisoformat(rock["adopted_at"])
    for v in visits:
        t = dt.datetime.fromisoformat(v["ts"])
        if v["accepted"]:
            notes.append(f"Visit on {_when(v['ts'])}, {(t - prev).days} days after the previous one.")
            prev = t
        else:
            notes.append(f"On {_when(v['ts'])} they photographed some other rock and claimed it was you.")
    gap = (dt.datetime.now().astimezone() - prev).days
    notes.append(f"It has now been {gap} days since they last came.")

    log(f"topping up {rock['name']}: chapters {first}-{first + CHAPTER_BATCH - 1}...")
    if mock:
        data = _mock_continue(CHAPTER_BATCH, first)
    else:
        data = gemma.ask_json(
            prompts.continue_prompt(rock["name"], dict(personality, backstory=rock["backstory"]),
                                    [ch["text"] for ch in chapters], notes, CHAPTER_BATCH),
            prompts.continue_schema(CHAPTER_BATCH),
            image=MEDIA_DIR / rock["photo_path"],
            check=prompts.continue_problems(CHAPTER_BATCH),
            max_tokens=700,  # 3 short chapters are ~300 tokens
        )
    new = [(first + i, t, render(rock, t, f"ch{first + i}")) for i, t in enumerate(data["chapters"][:CHAPTER_BATCH])]
    ts = now()
    with db.tx() as c:
        c.executemany(
            "INSERT OR IGNORE INTO chapters(rock_id, number, text, audio_path, created_at) VALUES(?,?,?,?,?)",
            [(rock_id, n, t, a, ts) for n, t, a in new],
        )
    log(f"  {rock['name']} has {len(new)} new chapters.")


def backfill_audio():
    """Voice anything that was written before Piper was installed."""
    if not voice.available():
        return
    with db.tx() as c:
        for r in c.execute("SELECT id FROM rocks WHERE status='awake' AND piper_voice IS NULL").fetchall():
            c.execute("UPDATE rocks SET piper_voice=?, voice_speed=? WHERE id=?",
                      (pick_voice(c, r["id"]), voice.speed_for(r["id"]), r["id"]))
        rocks = {r["id"]: r for r in c.execute("SELECT * FROM rocks").fetchall()}
        todo_ch = c.execute("SELECT rock_id, number, text FROM chapters WHERE audio_path IS NULL").fetchall()
        todo_ln = c.execute("SELECT rock_id, kind, tier, text FROM lines WHERE audio_path IS NULL").fetchall()
    if todo_ch or todo_ln:
        log(f"rendering {len(todo_ch) + len(todo_ln)} missing voice lines...")
    for row in todo_ch:
        a = render(rocks[row["rock_id"]], row["text"], f"ch{row['number']}")
        with db.tx() as c:
            c.execute("UPDATE chapters SET audio_path=? WHERE rock_id=? AND number=?", (a, row["rock_id"], row["number"]))
    for row in todo_ln:
        a = render(rocks[row["rock_id"]], row["text"], f"{row['kind']}-{row['tier']}".rstrip("-"))
        with db.tx() as c:
            c.execute("UPDATE lines SET audio_path=? WHERE rock_id=? AND kind=? AND tier=?",
                      (a, row["rock_id"], row["kind"], row["tier"]))


def run(mock: bool = False) -> bool:
    """Returns False if a run is already in progress."""
    if not _lock.acquire(blocking=False):
        return False
    status.update(running=True, started_at=now(), finished_at=None, log=[])
    try:
        if not mock and not gemma.available():
            log(f"Gemma isn't available. Start Ollama and run: ollama pull {GEMMA_MODEL}  (or use mock mode)")
            return True
        if not voice.available():
            log("Piper voices not found; rocks will wake up text-only. Run scripts/fetch_assets.py for voices.")
        with db.tx() as c:
            sleeping = [r["id"] for r in c.execute("SELECT id FROM rocks WHERE status='sleeping' ORDER BY adopted_at")]
            awake = [r["id"] for r in c.execute("SELECT id FROM rocks WHERE status='awake' ORDER BY adopted_at")]
        if not sleeping and not awake:
            log("No rocks adopted yet. Go outside.")
        status["sleeping"] = len(sleeping)
        for rid in sleeping:
            try:
                wake_rock(rid, mock)
                _failed_at.pop(rid, None)
            except Exception as e:  # one grumpy rock shouldn't block the rest
                _failed_at[rid] = time.time()
                log(f"  failed to wake {rid[:8]}: {e!r}")
            status["sleeping"] -= 1
        for rid in awake:
            try:
                top_up_rock(rid, mock)
                _failed_at.pop(rid, None)
            except Exception as e:
                _failed_at[rid] = time.time()
                log(f"  failed to top up {rid[:8]}: {e!r}")
        backfill_audio()
        log("done. The rocks are thinking.")
        return True
    finally:
        status.update(running=False, finished_at=now())
        _lock.release()


def has_work() -> bool:
    """A rock is asleep, or about to run out of chapters (and Gemma hasn't just failed on it)."""
    with db.tx() as c:
        rows = c.execute(
            "SELECT r.id, r.status, (SELECT COUNT(*) FROM chapters ch WHERE ch.rock_id=r.id AND ch.unlocked_at IS NULL) AS locked"
            " FROM rocks r"
        ).fetchall()
    recent = lambda rid: time.time() - _failed_at.get(rid, 0) < RETRY_AFTER_S
    return any((r["status"] == "sleeping" or r["locked"] < CHAPTER_LOW_WATER) and not recent(r["id"]) for r in rows)


def start(mock: bool = MOCK_BRAIN) -> bool:
    """Run the job in the background. False if one is already running."""
    if status["running"]:
        return False
    status["running"] = True  # claim it now, so two syncs a second apart don't both start a run
    threading.Thread(target=run, kwargs={"mock": mock}, daemon=True).start()
    return True


def rewake(rock_id: str):
    with db.tx() as c:
        c.execute("DELETE FROM chapters WHERE rock_id=?", (rock_id,))
        c.execute("DELETE FROM lines WHERE rock_id=?", (rock_id,))
        c.execute("UPDATE rocks SET status='sleeping', name=NULL, personality=NULL, backstory=NULL WHERE id=?", (rock_id,))


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--mock", action="store_true", help="canned text instead of Gemma")
    ap.add_argument("--rewake", metavar="ROCK_ID", help="reset one rock (id prefix ok) to sleeping before running")
    args = ap.parse_args()
    db.init()
    if args.rewake:
        with db.tx() as c:
            ids = [r["id"] for r in c.execute("SELECT id FROM rocks WHERE id LIKE ?", (args.rewake + "%",))]
        if len(ids) != 1:
            raise SystemExit(f"--rewake {args.rewake!r} matched {len(ids)} rocks")
        rewake(ids[0])
    run(mock=args.mock)
