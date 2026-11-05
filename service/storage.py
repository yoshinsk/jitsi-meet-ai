# File: service/storage.py
# Summary: Private settings, room capabilities, transcripts, and durable recording jobs.
"""SQLite stores room state; only salted hashes of meeting capabilities are persisted."""

import hashlib
import json
import os
from pathlib import Path
import secrets
import sqlite3
import time

DATA = Path(os.environ.get("JITSI_AI_DATA", "/var/lib/jitsi-ai"))
RECORDINGS = Path(os.environ.get("JITSI_RECORDINGS", "/srv/jitsi-recordings"))
ORIGIN = os.environ.get("JITSI_AI_ORIGIN", "https://" + os.environ.get("JITSI_DOMAIN", "meet.example.com"))
STATIC = Path(__file__).resolve().parent.parent / "static"
LANGUAGES = {"ja": "日本語", "en": "English", "zh": "中文", "ko": "한국어", "vi": "Tiếng Việt"}
DEFAULTS = {"api_key": "", "transcription_model": "gpt-transcribe",
            "recording_transcription_model": "gpt-transcribe",
            "subtitle_model": "gpt-4o-mini",
            "translation_model": "gpt-realtime-translate", "minutes_model": "gpt-5-mini",
            "auto_minutes": True, "max_speakers": 4, "audio_minutes": 120}


def db():
    """Use short transactions so WebSockets and the background worker can share one database."""
    conn = sqlite3.connect(DATA / "meetings.sqlite3", timeout=10)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


def init():
    """Create private storage without changing or truncating existing meetings."""
    DATA.mkdir(parents=True, exist_ok=True, mode=0o700)
    with db() as conn:
        conn.executescript("""
          PRAGMA journal_mode=WAL;
          CREATE TABLE IF NOT EXISTS rooms (
            id TEXT PRIMARY KEY, title TEXT NOT NULL, created REAL NOT NULL,
            expires REAL NOT NULL, closed INTEGER NOT NULL DEFAULT 0,
            audio_seconds REAL NOT NULL DEFAULT 0, notes TEXT NOT NULL DEFAULT ''
          );
          CREATE TABLE IF NOT EXISTS capabilities (
            hash TEXT PRIMARY KEY, room TEXT REFERENCES rooms(id), role TEXT NOT NULL
          );
          CREATE TABLE IF NOT EXISTS segments (
            id INTEGER PRIMARY KEY, room TEXT REFERENCES rooms(id), speaker TEXT,
            language TEXT, kind TEXT, text TEXT, created REAL
          );
          CREATE TABLE IF NOT EXISTS jobs (
            id TEXT PRIMARY KEY, media TEXT UNIQUE, room TEXT, status TEXT NOT NULL,
            transcript TEXT NOT NULL DEFAULT '', notes TEXT NOT NULL DEFAULT '',
            error TEXT NOT NULL DEFAULT '', created REAL, updated REAL
          );
          CREATE TABLE IF NOT EXISTS bridge_sessions (
            meeting_id TEXT PRIMARY KEY, room TEXT REFERENCES rooms(id), participants TEXT NOT NULL DEFAULT '{}',
            active INTEGER NOT NULL DEFAULT 1, updated REAL, input_frames INTEGER NOT NULL DEFAULT 0,
            output_frames INTEGER NOT NULL DEFAULT 0, transcripts INTEGER NOT NULL DEFAULT 0,
            translation_captions INTEGER NOT NULL DEFAULT 0, last_error TEXT NOT NULL DEFAULT ''
          );
          CREATE TABLE IF NOT EXISTS bridge_messages (
            meeting_id TEXT, message_id TEXT, PRIMARY KEY(meeting_id,message_id)
          );
        """)
        # An interrupted process is recoverable; completed transcriptions are retained for retries.
        conn.execute("UPDATE jobs SET status='pending' WHERE status='processing'")


def settings():
    """Never expose this result directly: it contains server credentials."""
    path = DATA / "settings.json"
    return {**DEFAULTS, **(json.loads(path.read_text(encoding="utf-8")) if path.exists() else {})}


def save_settings(values):
    """Replace the settings atomically and restrict the final file to its service owner."""
    path = DATA / "settings.json"
    temporary = DATA / ("settings-" + secrets.token_hex(8) + ".tmp")
    temporary.write_text(json.dumps(values, ensure_ascii=False, indent=2), encoding="utf-8")
    temporary.chmod(0o600)
    os.replace(temporary, path)


def digest(value):
    """Capabilities have 256 bits of randomness, so a SHA-256 digest safely supports lookup."""
    return hashlib.sha256(value.encode()).hexdigest()


def new_capability(room, role):
    token = secrets.token_urlsafe(32)
    with db() as conn:
        conn.execute("INSERT INTO capabilities VALUES (?,?,?)", (digest(token), room, role))
    return token


def room_access(room, token):
    with db() as conn:
        row = conn.execute("SELECT r.*, c.role FROM rooms r JOIN capabilities c ON c.room=r.id "
                           "WHERE r.id=? AND c.hash=? AND r.expires>?",
                           (room, digest(token), time.time())).fetchone()
    return dict(row) if row else None


def segment(room, speaker, language, kind, text):
    """Store finalized text only; audio chunks and partial captions are not retained."""
    if not text.strip():
        return
    with db() as conn:
        conn.execute("INSERT INTO segments(room,speaker,language,kind,text,created) VALUES (?,?,?,?,?,?)",
                     (room, speaker[:80], language[:32], kind, text[:20000], time.time()))


def transcript(room):
    with db() as conn:
        rows = conn.execute("SELECT speaker,text FROM segments WHERE room=? AND kind='source' ORDER BY id",
                            (room,)).fetchall()
    return "\n".join(f"{row['speaker']}: {row['text']}" for row in rows)


def recording_path(value):
    """Reject traversal and symlinks escaping the recording directory, including database tampering."""
    path = Path(value).resolve()
    if not path.is_relative_to(RECORDINGS.resolve()) or not path.is_file() or path.suffix != ".mp4":
        raise ValueError("Invalid recording path")
    return path
