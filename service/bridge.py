# File: service/bridge.py
# Summary: Authenticate native Jitsi media sessions, enforce room budgets, and relay the official Opus proxy protocol.

import asyncio
import base64
from contextlib import suppress
import ctypes
import ctypes.util
import json
import secrets
import time
from urllib.parse import urlencode

import websockets
from fastapi import APIRouter, Header, HTTPException, Request, WebSocket, WebSocketDisconnect
from pydantic import BaseModel, Field

from . import storage as store

router = APIRouter()
ACTIVE = {}


def trusted(value):
    """This shared token belongs only to Jicofo and Prosody; it is unrelated to the OpenAI API key."""
    expected = store.settings().get("bridge_token", "")
    return bool(expected) and secrets.compare_digest(value.removeprefix("Bearer "), expected)


class Registration(BaseModel):
    room: str = Field(pattern=r"^ai-[a-f0-9]{32}$")
    meeting_id: str = Field(min_length=8, max_length=128, pattern=r"^[a-zA-Z0-9_-]+$")
    participants: dict[str, str] = Field(default_factory=dict)
    finished: bool = False


@router.post("/internal/bridge/register")
def register(value: Registration, request: Request, authorization: str = Header(default="")):
    if request.client.host not in {"127.0.0.1", "::1", "testclient"} or not trusted(authorization):
        raise HTTPException(403)
    room = value.room.removeprefix("ai-")
    participants = {str(key)[:80]: str(name)[:80] for key, name in list(value.participants.items())[:32]}
    with store.db() as conn:
        row = conn.execute("SELECT closed,expires FROM rooms WHERE id=?", (room,)).fetchone()
        if not row or row["closed"] or row["expires"] < time.time():
            raise HTTPException(403, "AI会議として登録されていません。")
        conn.execute("UPDATE bridge_sessions SET active=0 WHERE room=? AND meeting_id<>?", (room,value.meeting_id))
        conn.execute("INSERT INTO bridge_sessions(meeting_id,room,participants,active,updated) VALUES (?,?,?,?,?) "
                     "ON CONFLICT(meeting_id) DO UPDATE SET participants=excluded.participants,active=excluded.active,updated=excluded.updated",
                     (value.meeting_id, room, json.dumps(participants, ensure_ascii=False), int(not value.finished), time.time()))
    if value.finished and store.settings()["auto_minutes"] and store.transcript(room).strip():
        from .app import queue_live_minutes
        queue_live_minutes(room)
    return {"enabled": not value.finished, "languages": list(store.LANGUAGES)}


def session(meeting):
    with store.db() as conn:
        value = conn.execute("SELECT b.*,r.closed,r.expires,r.audio_seconds FROM bridge_sessions b JOIN rooms r ON r.id=b.room "
                             "WHERE meeting_id=?", (meeting,)).fetchone()
    return dict(value) if value else None


def allowed(value):
    return (value and value["active"] and not value["closed"] and value["expires"] > time.time()
            and value["audio_seconds"] < store.settings()["audio_minutes"] * 60
            and bool(store.settings()["api_key"]))


def opus_duration(payload):
    """Read packet duration with the same libopus used by Jitsi; never infer duration from compressed byte count."""
    if not hasattr(opus_duration, "library"):
        library = ctypes.CDLL(ctypes.util.find_library("opus") or "libopus.so.0")
        library.opus_packet_get_nb_samples.argtypes = [ctypes.c_char_p, ctypes.c_int32, ctypes.c_int32]
        library.opus_packet_get_nb_samples.restype = ctypes.c_int
        opus_duration.library = library
    packet = base64.b64decode(payload, validate=True)
    samples = opus_duration.library.opus_packet_get_nb_samples(packet, len(packet), 48000)
    if not 0 < samples <= 5760:
        raise ValueError("Invalid Opus duration")
    return samples / 48000


def save_result(meeting, value, event):
    """Persist final source transcripts once; translation deltas are delivered directly to Jitsi clients."""
    translated = event.get("type") == "realtime-translation-result"
    with store.db() as conn:
        if event.get("event") == "media":
            conn.execute("UPDATE bridge_sessions SET output_frames=output_frames+1 WHERE meeting_id=?", (meeting,))
        elif translated:
            conn.execute("UPDATE bridge_sessions SET translation_captions=translation_captions+1 WHERE meeting_id=?", (meeting,))
        elif event.get("type") == "transcription-result" and not event.get("is_interim"):
            message = str(event.get("message_id", ""))[:200]
            inserted = conn.execute("INSERT OR IGNORE INTO bridge_messages VALUES (?,?)", (meeting, message)).rowcount
            if inserted:
                conn.execute("UPDATE bridge_sessions SET transcripts=transcripts+1 WHERE meeting_id=?", (meeting,))
            else:
                return
        else:
            return
    if event.get("type") == "transcription-result" and not event.get("is_interim"):
        participant = str(event.get("participant", {}).get("id", ""))
        names = json.loads(value["participants"])
        speaker = names.get(participant, names.get(participant.split("-", 1)[0], participant or "発言者不明"))
        text = "".join(item.get("text", "") for item in event.get("transcript", []))
        store.segment(value["room"], speaker, event.get("language", "auto"), "source", text)


@router.websocket("/internal/bridge/{kind}")
async def relay(ws: WebSocket, kind: str):
    """JVB owns the microphones and playback tracks; official Android/iOS clients use their existing controls."""
    meeting = ws.query_params.get("sessionId", "")
    value = session(meeting)
    if (kind not in {"transcribe", "translate"} or not trusted(ws.headers.get("authorization", ""))
            or ws.client.host not in {"127.0.0.1", "::1"} or not allowed(value)):
        await ws.close(code=1008)
        return
    identity = secrets.token_hex(8)
    sources = set()
    ACTIVE[identity] = {"kind":kind,"room":value["room"],"ws":ws,"sources":sources}
    await ws.accept()
    seconds = 0.0
    counts = 0
    last_check = time.monotonic()
    upstream = None
    tasks = []
    try:
        query = urlencode({"sessionId": meeting, "sendBack": "true", "sendBackInterim": "true"})
        upstream = await websockets.connect(f"ws://127.0.0.1:8766/{kind}?{query}", max_size=2_000_000, open_timeout=15)
        with store.db() as conn:
            conn.execute("UPDATE bridge_sessions SET last_error='' WHERE meeting_id=?",(meeting,))

        async def send_audio():
            nonlocal seconds, counts, last_check, value

            def admit(tag):
                if tag in sources:
                    return True
                limit = store.settings()["max_speakers"]
                total = sum(len(item.get("sources", [])) for item in ACTIVE.values() if item["kind"] == "translate")
                if len(sources) >= limit or (kind == "translate" and total >= limit):
                    return False
                sources.add(tag)
                return True

            while True:
                raw = await ws.receive_text()
                event = json.loads(raw)
                if event.get("event") in {"start", "sources"}:
                    value = session(meeting)
                    if not allowed(value):
                        return
                if event.get("event") == "sources":
                    # Per-source requests open paid translation directions before the first audio packet arrives.
                    exports = [tag for tag in event.get("exports", []) if admit(tag)]
                    event["exports"] = exports
                    event["requests"] = [tag for tag in event.get("requests", []) if tag.rsplit('.',1)[0] in exports]
                    raw = json.dumps(event)
                if event.get("event") == "start":
                    tag = event.get("start", {}).get("tag", "")
                    if not admit(tag):
                        continue
                if event.get("event") == "stop":
                    tag = event.get("stop", {}).get("tag", "")
                    sources.discard(tag)
                if event.get("event") == "media":
                    media = event.get("media", {})
                    if not admit(media.get("tag")):
                        continue
                    seconds += opus_duration(media["payload"])
                    counts += 1
                if time.monotonic() - last_check >= 1:
                    with store.db() as conn:
                        conn.execute("UPDATE rooms SET audio_seconds=audio_seconds+? WHERE id=?", (seconds, value["room"]))
                        conn.execute("UPDATE bridge_sessions SET input_frames=input_frames+?,updated=? WHERE meeting_id=?",
                                     (counts, time.time(), meeting))
                    seconds, counts, last_check = 0.0, 0, time.monotonic()
                    value = session(meeting)
                    if not allowed(value):
                        return
                await upstream.send(raw)

        async def send_results():
            async for raw in upstream:
                event = json.loads(raw)
                save_result(meeting, value, event)
                await ws.send_text(raw)

        tasks = [asyncio.create_task(send_audio()), asyncio.create_task(send_results())]
        done, _ = await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
        for task in done:
            task.result()
    except WebSocketDisconnect:
        pass
    except Exception:
        with store.db() as conn:
            conn.execute("UPDATE bridge_sessions SET last_error=? WHERE meeting_id=?",
                         ("通訳・文字起こしの接続を確認してください。", meeting))
    finally:
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        if upstream:
            await upstream.close()
        if seconds or counts:
            with store.db() as conn:
                conn.execute("UPDATE rooms SET audio_seconds=audio_seconds+? WHERE id=?", (seconds, value["room"]))
                conn.execute("UPDATE bridge_sessions SET input_frames=input_frames+? WHERE meeting_id=?", (counts, meeting))
        ACTIVE.pop(identity, None)
        with suppress(Exception):
            await ws.close()


async def close_streams(room=None):
    """Stop paid native-media sessions when the host closes a room or the administrator removes the key."""
    streams=[item["ws"] for item in list(ACTIVE.values()) if room is None or item["room"]==room]
    await asyncio.gather(*(ws.close(code=1000) for ws in streams),return_exceptions=True)
