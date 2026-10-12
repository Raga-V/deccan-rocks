"""Home base. The phone syncs here over Wi-Fi; nothing ever goes to the internet."""
import base64
import datetime as dt
import json
import shutil
import threading
import time
from contextlib import asynccontextmanager
from typing import Annotated

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field, StringConstraints

from . import db, gemma, voice, wake
from .config import CERTS_DIR, GEMMA_MODEL, MEDIA_DIR, NIGHTLY_WAKE, PWA_DIR, VENDOR_DIR

Id = Annotated[str, StringConstraints(pattern=r"^[A-Za-z0-9-]{8,64}$")]  # ids become folder names
MAX_EMBEDDINGS = 8  # adoption photo + the most recent accepted visits



def next_nightly_wake() -> dt.datetime | None:
    if NIGHTLY_WAKE.lower() == "off":
        return None
    h, m = map(int, NIGHTLY_WAKE.split(":"))
    now = dt.datetime.now()
    at = now.replace(hour=h, minute=m, second=0, microsecond=0)
    return at if at > now else at + dt.timedelta(days=1)


def nightly_loop():
    """Rocks take a night to think: wake them while the human sleeps (if the laptop is on)."""
    while (at := next_nightly_wake()) is not None:
        while (left := (at - dt.datetime.now()).total_seconds()) > 0:
            time.sleep(min(left, 60))  # short naps, so a laptop resuming from sleep doesn't overshoot by hours
        wake.log(f"nightly wake ({NIGHTLY_WAKE})")
        wake.run()


@asynccontextmanager
async def lifespan(_app):
    threading.Thread(target=nightly_loop, daemon=True, name="nightly-wake").start()
    yield


app = FastAPI(title="Deccan Rocks", lifespan=lifespan)
db.init()


# ---------------------------------------------------------------- models

class RockIn(BaseModel):
    id: Id
    photo: str                                  # data URL (JPEG)
    embeddings: list[list[float]]
    lat: float | None = None
    lng: float | None = None
    gps_accuracy_m: float | None = None
    adopted_at: str
    spot: str | None = None


class VisitIn(BaseModel):
    id: Id
    rock_id: Id | None = None
    ts: str
    photo: str | None = None
    embedding: list[float] | None = None
    similarity: float | None = None
    gps_distance_m: float | None = None
    gps_accuracy_m: float | None = None
    accepted: bool
    chapter_number: int | None = None
    reason: str | None = None


class SampleIn(BaseModel):
    id: Id
    ts: str
    truth: Id | None = None
    scores: dict[str, dict]


class WalkIn(BaseModel):
    id: Id
    started_at: str
    ended_at: str
    screen_on_s: float
    visits: int


class SyncIn(BaseModel):
    rocks: list[RockIn] = Field(default_factory=list)
    visits: list[VisitIn] = Field(default_factory=list)
    samples: list[SampleIn] = Field(default_factory=list)
    walks: list[WalkIn] = Field(default_factory=list)
    human_name: str | None = None
    released: list[Id] = Field(default_factory=list)  # rocks the human let go of


# ---------------------------------------------------------------- helpers

def save_data_url(data_url: str, rel_path: str) -> str:
    try:
        payload = base64.b64decode(data_url.split(",", 1)[1])
    except Exception:
        raise HTTPException(400, f"bad image data for {rel_path}")
    out = MEDIA_DIR / rel_path
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_bytes(payload)
    return rel_path


def media_url(rel: str | None) -> str | None:
    return f"/media/{rel}" if rel else None


def rock_out(c, r) -> dict:
    chapters = c.execute("SELECT * FROM chapters WHERE rock_id=? ORDER BY number", (r["id"],)).fetchall()
    lines = c.execute("SELECT * FROM lines WHERE rock_id=?", (r["id"],)).fetchall()
    rejection = next((l for l in lines if l["kind"] == "rejection"), None)
    return {
        "id": r["id"], "status": r["status"], "name": r["name"], "spot": r["spot"],
        "lat": r["lat"], "lng": r["lng"], "gps_accuracy_m": r["gps_accuracy_m"],
        "adopted_at": r["adopted_at"], "last_visit_at": r["last_visit_at"], "woke_at": r["woke_at"],
        "personality": json.loads(r["personality"]) if r["personality"] else None,
        "backstory": r["backstory"], "voice": r["piper_voice"],
        "photo_url": media_url(r["photo_path"]),
        "embeddings": json.loads(r["embeddings"]),
        "chapters": [
            {"number": ch["number"], "text": ch["text"], "audio_url": media_url(ch["audio_path"]),
             "unlocked_at": ch["unlocked_at"]}
            for ch in chapters
        ],
        "rejection": rejection and {"text": rejection["text"], "audio_url": media_url(rejection["audio_path"])},
        "nags": sorted(
            ({"tier": l["tier"], "days": l["days"], "text": l["text"], "audio_url": media_url(l["audio_path"])}
             for l in lines if l["kind"] == "nag"),
            key=lambda n: n["days"],
        ),
    }


# ---------------------------------------------------------------- API

@app.get("/api/health")
def health():
    return {
        "ok": True,
        "gemma": gemma.available(), "model": GEMMA_MODEL,
        "voices": voice.installed_voices() if voice.PiperVoice else [],
        "waking": wake.status["running"],
        "nightly_wake": NIGHTLY_WAKE if next_nightly_wake() else None,
    }


@app.post("/api/sync")
def sync(body: SyncIn):
    """Phone pushes what it collected in the field; gets back everything the rocks have thought since."""
    with db.tx() as c:
        if body.human_name:
            db.set_setting(c, "human_name", body.human_name.strip()[:40])

        for rid in body.released:
            c.execute("UPDATE visits SET rock_id=NULL WHERE rock_id=?", (rid,))
            for table in ("chapters", "lines"):
                c.execute(f"DELETE FROM {table} WHERE rock_id=?", (rid,))
            c.execute("DELETE FROM rocks WHERE id=?", (rid,))
            shutil.rmtree(MEDIA_DIR / rid, ignore_errors=True)

        for r in body.rocks:
            if r.id in body.released:
                continue
            if c.execute("SELECT 1 FROM rocks WHERE id=?", (r.id,)).fetchone():
                continue  # already have it; re-sent because the phone missed our ack
            photo = save_data_url(r.photo, f"{r.id}/adopted.jpg")
            c.execute(
                "INSERT INTO rocks(id, photo_path, embeddings, lat, lng, gps_accuracy_m, adopted_at, spot) VALUES(?,?,?,?,?,?,?,?)",
                (r.id, photo, json.dumps(r.embeddings[:MAX_EMBEDDINGS]), r.lat, r.lng, r.gps_accuracy_m,
                 r.adopted_at, (r.spot or "").strip()[:80] or None),
            )

        for v in sorted(body.visits, key=lambda v: v.ts):
            if c.execute("SELECT 1 FROM visits WHERE id=?", (v.id,)).fetchone():
                continue
            rock = v.rock_id and c.execute("SELECT * FROM rocks WHERE id=?", (v.rock_id,)).fetchone()
            rock_id = rock["id"] if rock else None
            photo = save_data_url(v.photo, f"{rock_id or '_unmatched'}/visit-{v.id}.jpg") if v.photo else None
            c.execute(
                "INSERT INTO visits(id, rock_id, ts, photo_path, similarity, gps_distance_m, gps_accuracy_m, accepted, chapter_number, reason)"
                " VALUES(?,?,?,?,?,?,?,?,?,?)",
                (v.id, rock_id, v.ts, photo, v.similarity, v.gps_distance_m, v.gps_accuracy_m,
                 int(v.accepted), v.chapter_number, v.reason),
            )
            if rock and v.accepted:
                c.execute("UPDATE rocks SET last_visit_at=MAX(COALESCE(last_visit_at, ''), ?) WHERE id=?", (v.ts, rock_id))
                if v.chapter_number:
                    c.execute("UPDATE chapters SET unlocked_at=? WHERE rock_id=? AND number=? AND unlocked_at IS NULL",
                              (v.ts, rock_id, v.chapter_number))
                if v.embedding:
                    embs = json.loads(rock["embeddings"])
                    embs = [embs[0], *(embs[1:] + [v.embedding])[-(MAX_EMBEDDINGS - 1):]]  # always keep the adoption photo
                    c.execute("UPDATE rocks SET embeddings=? WHERE id=?", (json.dumps(embs), rock_id))

        for s in body.samples:
            c.execute("INSERT OR IGNORE INTO samples(id, ts, truth, scores) VALUES(?,?,?,?)",
                      (s.id, s.ts, s.truth, json.dumps(s.scores)))
        for w in body.walks:
            c.execute("INSERT OR IGNORE INTO walks(id, started_at, ended_at, screen_on_s, visits) VALUES(?,?,?,?,?)",
                      (w.id, w.started_at, w.ended_at, w.screen_on_s, w.visits))

        rocks = [rock_out(c, r) for r in c.execute("SELECT * FROM rocks ORDER BY adopted_at").fetchall()]

    return {
        "server_time": dt.datetime.now().astimezone().isoformat(timespec="seconds"),
        "rocks": rocks,
        "acked": {
            "rocks": [r.id for r in body.rocks], "visits": [v.id for v in body.visits],
            "samples": [s.id for s in body.samples], "walks": [w.id for w in body.walks],
            "released": body.released,
        },
    }


@app.get("/api/rocks")
def list_rocks():
    with db.tx() as c:
        return [rock_out(c, r) for r in c.execute("SELECT * FROM rocks ORDER BY adopted_at").fetchall()]


@app.post("/api/wake")
def start_wake(mock: bool = False):
    if wake.status["running"]:
        return {"started": False, "status": wake.status}
    threading.Thread(target=wake.run, kwargs={"mock": mock}, daemon=True).start()
    return {"started": True}


@app.get("/api/wake")
def wake_status():
    return wake.status


@app.get("/rootCA.crt")
def root_ca():
    """So the phone can install the local CA (see README > Phone setup)."""
    ca = CERTS_DIR / "rootCA.pem"
    if not ca.exists():
        raise HTTPException(404, "No local CA yet. Run: python scripts/make_cert.py")
    return FileResponse(ca, media_type="application/x-x509-ca-cert", filename="deccan-rocks-rootCA.crt")


# ---------------------------------------------------------------- static

VENDOR_DIR.mkdir(exist_ok=True)
app.mount("/media", StaticFiles(directory=MEDIA_DIR), name="media")
app.mount("/vendor", StaticFiles(directory=VENDOR_DIR), name="vendor")
app.mount("/", StaticFiles(directory=PWA_DIR, html=True), name="pwa")
