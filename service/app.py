# File: service/app.py
# Summary: Authenticated Japanese administration, expiring meeting invites, and protected recordings/minutes.

import asyncio
from contextlib import asynccontextmanager, suppress
import csv
import hashlib
import io
import json
import re
import secrets
import time
from urllib.parse import quote

from fastapi import Depends, FastAPI, Header, HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse, Response
from fastapi.security import HTTPBasic, HTTPBasicCredentials
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from . import ai, bridge, storage as store

basic = HTTPBasic()


@asynccontextmanager
async def lifespan(app):
    store.init()
    worker = asyncio.create_task(ai.recording_worker())
    yield
    worker.cancel()
    with suppress(asyncio.CancelledError):
        await worker


app = FastAPI(lifespan=lifespan, docs_url=None, redoc_url=None, openapi_url=None)


@app.middleware("http")
async def boundaries(request: Request, call_next):
    """Block cross-site administrative writes even when the browser caches HTTP Basic credentials."""
    internal = (request.url.path == "/internal/bridge/register"
                and request.client.host in {"127.0.0.1", "::1", "testclient"}
                and bridge.trusted(request.headers.get("authorization", "")))
    if request.method not in {"GET", "HEAD", "OPTIONS"} and not internal and request.headers.get("origin") != store.ORIGIN:
        return JSONResponse({"detail": "操作元を確認できません。画面を再読み込みしてください。"}, status_code=403)
    response = await call_next(request)
    response.headers["Cache-Control"] = "no-store"
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["Referrer-Policy"] = "no-referrer"
    response.headers["X-Frame-Options"] = "SAMEORIGIN"
    return response


@app.exception_handler(ai.ProviderError)
async def provider_error(request, exc):
    return JSONResponse({"detail": str(exc)}, status_code=502)


def admin(credentials: HTTPBasicCredentials = Depends(basic)):
    """The SSH password is never used for an SSH login from the application; verify only its one-way hash."""
    config = store.settings()
    salt = config.get("admin_salt", "")
    calculated = hashlib.pbkdf2_hmac("sha256", credentials.password.encode(), salt.encode(), 600000).hex()
    if (not salt or not secrets.compare_digest(credentials.username.encode(), b"ubuntu")
            or not secrets.compare_digest(calculated, config.get("admin_digest", ""))):
        raise HTTPException(401, "管理者認証が必要です。", headers={"WWW-Authenticate": 'Basic realm="Jitsi AI administration"'})
    return credentials.username


def access(room, authorization):
    token = authorization.removeprefix("Bearer ") if authorization else ""
    result = store.room_access(room, token)
    if not result:
        raise HTTPException(403, "招待リンクが無効か、有効期限が切れています。")
    return result


def host_access(room, authorization):
    result = access(room, authorization)
    if result["role"] != "host":
        raise HTTPException(403, "主催者だけが実行できます。")
    return result


@app.get("/ai/api/status")
def status():
    return {"configured": bool(store.settings()["api_key"]), "languages": store.LANGUAGES,
            "service": "Jitsi AI companion", "version": "1.2.1", "transport":"jitsi-bridge"}


@app.get("/ai/")
def home():
    return FileResponse(store.STATIC / "index.html")


@app.get("/ai/admin", dependencies=[Depends(admin)])
def admin_page():
    return FileResponse(store.STATIC / "admin.html")


@app.get("/ai/room/{room}")
def meeting_page(room: str):
    if not re.fullmatch(r"[a-f0-9]{32}", room):
        raise HTTPException(404)
    return FileResponse(store.STATIC / "meeting.html")


@app.get("/ai/api/admin/settings", dependencies=[Depends(admin)])
def read_settings():
    config = store.settings()
    return {**{key: config[key] for key in store.DEFAULTS if key != "api_key"},
            "configured": bool(config["api_key"])}


class SettingsUpdate(BaseModel):
    api_key: str | None = Field(default=None, max_length=1024)
    transcription_model: str = Field(default="gpt-transcribe", pattern=r"^[a-zA-Z0-9._-]{1,120}$")
    recording_transcription_model: str = Field(default="gpt-transcribe", pattern=r"^[a-zA-Z0-9._-]{1,120}$")
    subtitle_model: str = Field(default="gpt-4o-mini", pattern=r"^[a-zA-Z0-9._-]{1,120}$")
    translation_model: str = Field(default="gpt-realtime-translate", pattern=r"^[a-zA-Z0-9._-]{1,120}$")
    minutes_model: str = Field(default="gpt-5-mini", pattern=r"^[a-zA-Z0-9._-]{1,120}$")
    auto_minutes: bool = True
    max_speakers: int = Field(default=4, ge=1, le=8)
    audio_minutes: int = Field(default=120, ge=1, le=480)


@app.put("/ai/api/admin/settings", dependencies=[Depends(admin)])
async def update_settings(values: SettingsUpdate):
    config = store.settings()
    update = values.model_dump(exclude={"api_key"})
    if values.api_key:
        if len(values.api_key) < 20 or any(char.isspace() for char in values.api_key):
            raise HTTPException(400, "APIキーの形式を確認してください。")
        update["api_key"] = values.api_key
    store.save_settings({**config, **update})
    return {"saved": True, "configured": bool(store.settings()["api_key"])}


@app.delete("/ai/api/admin/key", dependencies=[Depends(admin)])
async def delete_key():
    store.save_settings({**store.settings(), "api_key": ""})
    await bridge.close_streams()
    return {"deleted": True}


@app.post("/ai/api/admin/check-key", dependencies=[Depends(admin)])
async def check_key():
    """A models-list request checks authentication and listed access without generating paid content."""
    result = await ai.request("GET", "models")
    names = {item["id"] for item in result.get("data", [])}
    config = store.settings()
    return {"authenticated": True, "models": {config[key]: config[key] in names
            for key in ["transcription_model", "recording_transcription_model", "subtitle_model", "translation_model", "minutes_model"]},
            "notice": "認証とモデル一覧の確認結果です。残高・音声・翻訳品質の検証は含みません。"}


def model_catalog(models):
    """Filter account-visible IDs into API-compatible families; do not offer voice-agent models as interpreters."""
    names = sorted({model["id"] for model in models if isinstance(model.get("id"), str)})
    catalog = {key: [] for key in ["transcription_model", "recording_transcription_model", "subtitle_model", "translation_model", "minutes_model"]}
    for name in names:
        transcript = name.startswith(("gpt-transcribe", "gpt-live-transcribe", "gpt-4o-transcribe", "gpt-4o-mini-transcribe", "whisper-1"))
        diarize = "diarize" in name
        if transcript and not diarize:
            catalog["transcription_model"].append(name)
        if transcript and not name.startswith("gpt-live-transcribe"):
            catalog["recording_transcription_model"].append(name)
        if name.startswith("gpt-realtime-translate"):
            catalog["translation_model"].append(name)
        if (re.match(r"^(gpt-(4\.1|4o|5(?:\.\d+)?|6(?:\.\d+)?)|o[134])(?:-|$)", name)
            and not re.search(r"transcrib|whisper|realtime|audio|image|search|embedding|moderation|instruct|computer-use|tts|codex|chat-latest", name)
            and name not in {"gpt-4o-2024-05-13", "o1-preview", "o1-mini"}
            and not name.startswith(("o1-preview-", "o1-mini-"))):
            catalog["minutes_model"].append(name)
            # Pro reasoning models use Responses only; the upstream subtitle translator uses Chat Completions.
            if not re.search(r"-pro(?:-|$)", name):
                catalog["subtitle_model"].append(name)
    return {key: [{"id": name, "label": name + ("（旧モデル・2027/02/26終了予定）"
                    if name.startswith(("whisper-1", "gpt-4o-transcribe", "gpt-4o-mini-transcribe")) else "")}
                  for name in names] for key, names in catalog.items()}


@app.get("/ai/api/admin/models", dependencies=[Depends(admin)])
async def available_models():
    result = await ai.request("GET", "models")
    return {"models": model_catalog(result.get("data", [])), "fetched_at": time.time(),
            "notice": "このAPIキーで取得したモデルを用途別に表示しています。利用残高と音声品質は別途確認してください。"}


class RoomCreate(BaseModel):
    title: str = Field(min_length=1, max_length=120)


def links(room):
    return {"id": room, "host_url": f"{store.ORIGIN}/ai/room/{room}#host=" + store.new_capability(room, "host"),
            "conference_url":f"{store.ORIGIN}/ai-{room}",
            "ios_url":f"org.jitsi.meet://{store.ORIGIN.split('://')[1]}/ai-{room}",
            "android_url":f"intent://{store.ORIGIN.split('://')[1]}/ai-{room}#Intent;scheme=org.jitsi.meet;package=org.jitsi.meet;end",
            "guest_url": f"{store.ORIGIN}/ai/room/{room}#join=" + store.new_capability(room, "guest")}


@app.post("/ai/api/admin/rooms", dependencies=[Depends(admin)])
def create_room(value: RoomCreate):
    room = secrets.token_hex(16)
    with store.db() as conn:
        conn.execute("INSERT INTO rooms(id,title,created,expires) VALUES (?,?,?,?)",
                     (room, value.title.strip(), time.time(), time.time() + 86400))
    return links(room)


@app.get("/ai/api/admin/rooms", dependencies=[Depends(admin)])
def list_rooms():
    with store.db() as conn:
        return [dict(row) for row in conn.execute("SELECT * FROM rooms ORDER BY created DESC LIMIT 100")]


@app.post("/ai/api/admin/rooms/{room}/links", dependencies=[Depends(admin)])
def reissue_links(room: str):
    with store.db() as conn:
        row = conn.execute("SELECT * FROM rooms WHERE id=? AND closed=0 AND expires>?", (room, time.time())).fetchone()
    if not row:
        raise HTTPException(404, "利用可能な会議がありません。")
    return links(room)


@app.get("/ai/api/rooms/{room}")
def read_room(room: str, authorization: str = Header(default="")):
    result = access(room, authorization)
    with store.db() as conn:
        captions = [dict(row) for row in conn.execute(
            "SELECT speaker,language,kind,text,created FROM segments WHERE room=? ORDER BY id DESC LIMIT 80", (room,))]
    return {"id": room, "title": result["title"], "role": result["role"], "closed": bool(result["closed"]),
            "conference_url":f"{store.ORIGIN}/ai-{room}",
            "ios_url":f"org.jitsi.meet://{store.ORIGIN.split('://')[1]}/ai-{room}",
            "android_url":f"intent://{store.ORIGIN.split('://')[1]}/ai-{room}#Intent;scheme=org.jitsi.meet;package=org.jitsi.meet;end",
            "expires": result["expires"], "audio_seconds": result["audio_seconds"],
            "configured": bool(store.settings()["api_key"]), "captions": list(reversed(captions))}


@app.post("/ai/api/rooms/{room}/invite")
def invite_participant(room: str, authorization: str = Header(default="")):
    result = host_access(room, authorization)
    if result["closed"]:
        raise HTTPException(409, "会議は終了しています。")
    return {"guest_url": f"{store.ORIGIN}/ai/room/{room}#join=" + store.new_capability(room, "guest"),
            "conference_url":f"{store.ORIGIN}/ai-{room}"}


def queue_live_minutes(room):
    """A single unfinished job per room avoids duplicate billing from repeated button clicks."""
    with store.db() as conn:
        conn.execute("BEGIN IMMEDIATE")
        row = conn.execute("SELECT id FROM jobs WHERE room=? AND media IS NULL AND status IN ('pending','processing','waiting_key')",
                           (room,)).fetchone()
        if row:
            return row["id"]
        job_id = secrets.token_hex(16)
        conn.execute("INSERT INTO jobs(id,room,status,created,updated) VALUES (?,?,?,?,?)",
                     (job_id, room, "pending", time.time(), time.time()))
    return job_id


@app.post("/ai/api/rooms/{room}/minutes")
def generate_live_minutes(room: str, authorization: str = Header(default="")):
    host_access(room, authorization)
    if not store.transcript(room).strip():
        raise HTTPException(400, "文字起こしがまだありません。AIを開始して発言を記録してください。")
    return {"job": queue_live_minutes(room)}


@app.post("/ai/api/rooms/{room}/close")
async def close_room(room: str, authorization: str = Header(default="")):
    host_access(room, authorization)
    with store.db() as conn:
        conn.execute("UPDATE rooms SET closed=1 WHERE id=?", (room,))
    await bridge.close_streams(room)
    if store.transcript(room).strip():
        queue_live_minutes(room)
    return {"closed": True}


def job_public(row):
    value = dict(row)
    value["has_video"] = bool(value.get("media"))
    value.pop("media", None)
    value["notes"] = json.loads(value["notes"]) if value["notes"] else None
    return value


@app.get("/ai/api/admin/jobs", dependencies=[Depends(admin)])
def admin_jobs():
    with store.db() as conn:
        return [job_public(row) for row in conn.execute("SELECT * FROM jobs ORDER BY created DESC LIMIT 100")]


@app.get("/ai/api/rooms/{room}/jobs")
def room_jobs(room: str, authorization: str = Header(default="")):
    access(room, authorization)
    with store.db() as conn:
        return [job_public(row) for row in conn.execute("SELECT * FROM jobs WHERE room=? ORDER BY created DESC", (room,))]


@app.post("/ai/api/admin/jobs/{job}/retry", dependencies=[Depends(admin)])
def retry_job(job: str):
    with store.db() as conn:
        row = conn.execute("SELECT status FROM jobs WHERE id=?", (job,)).fetchone()
        if not row:
            raise HTTPException(404)
        if row["status"] == "processing":
            raise HTTPException(409, "現在処理中です。")
        conn.execute("UPDATE jobs SET status='pending',error='' WHERE id=?", (job,))
    return {"queued": True}


def get_job(job):
    with store.db() as conn:
        row = conn.execute("SELECT * FROM jobs WHERE id=?", (job,)).fetchone()
    if not row:
        raise HTTPException(404)
    return dict(row)


def export_job(job, kind):
    if kind == "video":
        try:
            path = store.recording_path(job["media"] or "")
        except ValueError:
            raise HTTPException(404) from None
        return FileResponse(path, media_type="video/mp4", filename=path.name)
    if kind == "transcript":
        content, filename = job["transcript"], "transcript.txt"
    elif kind == "minutes" and job["notes"]:
        content, filename = ai.markdown(json.loads(job["notes"])), "minutes.md"
    elif kind == "tasks" and job["notes"]:
        output = io.StringIO(newline="")
        writer = csv.writer(output)
        writer.writerow(["タスク", "担当者", "期限", "根拠"])
        for task in json.loads(job["notes"])["action_items"]:
            # Prefix formula-like cells before opening a task list in Excel.
            values = [task[key] for key in ["task", "owner", "due", "evidence"]]
            writer.writerow(["'" + value if value.lstrip().startswith(("=", "+", "-", "@")) else value for value in values])
        content, filename = "\ufeff" + output.getvalue(), "tasks.csv"
    else:
        raise HTTPException(404, "処理が完了するとダウンロードできます。")
    return Response(content, media_type="text/plain; charset=utf-8",
                    headers={"Content-Disposition": f'attachment; filename="{quote(filename)}"'})


@app.get("/ai/api/admin/jobs/{job}/download/{kind}", dependencies=[Depends(admin)])
def admin_download(job: str, kind: str):
    return export_job(get_job(job), kind)


@app.get("/ai/api/rooms/{room}/jobs/{job}/download/{kind}")
def participant_download(room: str, job: str, kind: str, authorization: str = Header(default="")):
    result = access(room, authorization)
    item = get_job(job)
    if item["room"] != room or (kind == "video" and result["role"] != "host"):
        raise HTTPException(403)
    return export_job(item, kind)


app.include_router(bridge.router)
app.mount("/ai/static", StaticFiles(directory=store.STATIC), name="static")
