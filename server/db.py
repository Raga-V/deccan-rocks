"""SQLite storage. One file, no ORM."""
import json
import sqlite3
from contextlib import contextmanager

from .config import DB_PATH

SCHEMA = """
CREATE TABLE IF NOT EXISTS rocks (
    id            TEXT PRIMARY KEY,          -- uuid minted on the phone
    status        TEXT NOT NULL DEFAULT 'sleeping',   -- sleeping | awake
    name          TEXT,
    spot          TEXT,                      -- optional place label typed at adoption
    photo_path    TEXT NOT NULL,
    embeddings    TEXT NOT NULL,             -- JSON list of 512-d CLIP vectors (adoption + accepted visits)
    lat REAL, lng REAL, gps_accuracy_m REAL,
    adopted_at    TEXT NOT NULL,
    personality   TEXT,                      -- JSON from Gemma
    backstory     TEXT,
    piper_voice   TEXT,
    voice_speed   REAL,                      -- Piper length_scale (>1 = slower)
    last_visit_at TEXT,
    woke_at       TEXT
);
CREATE TABLE IF NOT EXISTS chapters (
    rock_id     TEXT NOT NULL REFERENCES rocks(id),
    number      INTEGER NOT NULL,
    text        TEXT NOT NULL,
    audio_path  TEXT,
    created_at  TEXT NOT NULL,
    unlocked_at TEXT,
    PRIMARY KEY (rock_id, number)
);
-- Non-chapter voice lines: kind = 'rejection' | 'nag', tier = nag tier name or ''
CREATE TABLE IF NOT EXISTS lines (
    rock_id    TEXT NOT NULL REFERENCES rocks(id),
    kind       TEXT NOT NULL,
    tier       TEXT NOT NULL DEFAULT '',
    days       INTEGER,
    text       TEXT NOT NULL,
    audio_path TEXT,
    created_at TEXT NOT NULL,
    PRIMARY KEY (rock_id, kind, tier)
);
CREATE TABLE IF NOT EXISTS visits (
    id               TEXT PRIMARY KEY,
    rock_id          TEXT,                   -- matched / claimed rock (may be null if nothing nearby)
    ts               TEXT NOT NULL,
    photo_path       TEXT,
    similarity       REAL,
    gps_distance_m   REAL,
    gps_accuracy_m   REAL,
    accepted         INTEGER NOT NULL,
    chapter_number   INTEGER,                -- chapter unlocked by this visit
    reason           TEXT
);
-- Calibration samples: "this photo is rock X (or none)", with similarity to every rock.
CREATE TABLE IF NOT EXISTS samples (
    id         TEXT PRIMARY KEY,
    ts         TEXT NOT NULL,
    truth      TEXT,                         -- rock id, or null for "none of mine"
    scores     TEXT NOT NULL                 -- JSON {rock_id: {similarity, distance_m}}
);
CREATE TABLE IF NOT EXISTS walks (
    id          TEXT PRIMARY KEY,
    started_at  TEXT NOT NULL,
    ended_at    TEXT NOT NULL,
    screen_on_s REAL NOT NULL,
    visits      INTEGER NOT NULL
);
CREATE TABLE IF NOT EXISTS settings (
    key   TEXT PRIMARY KEY,
    value TEXT NOT NULL
);
"""


def connect() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH, timeout=30)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


@contextmanager
def tx():
    conn = connect()
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def init():
    with tx() as c:
        c.executescript(SCHEMA)


def get_setting(c, key, default=None):
    row = c.execute("SELECT value FROM settings WHERE key=?", (key,)).fetchone()
    return json.loads(row["value"]) if row else default


def set_setting(c, key, value):
    c.execute(
        "INSERT INTO settings(key, value) VALUES(?, ?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
        (key, json.dumps(value)),
    )
