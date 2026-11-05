# File: tests/test_service.py
# Summary: Verify authentication, meeting isolation, recording boundaries, replay-safe jobs, and API wire formats.

import asyncio
import base64
import hashlib
import json
from pathlib import Path
import time

import pytest
from fastapi.testclient import TestClient

from service import ai, auth, bridge, storage as store
from service.app import app, model_catalog


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setattr(store, "DATA", tmp_path / "state")
    monkeypatch.setattr(store, "RECORDINGS", tmp_path / "recordings")
    monkeypatch.setattr(store, "ORIGIN", "https://testserver")
    store.RECORDINGS.mkdir()
    store.init()
    store.save_settings({**store.DEFAULTS, "admin_salt": "test-salt",
        "admin_digest": hashlib.pbkdf2_hmac("sha256", b"test-password", b"test-salt", 600000).hex()})
    bridge.ACTIVE.clear()
    with TestClient(app, base_url=store.ORIGIN) as value:
        value.headers["Origin"] = store.ORIGIN
        yield value


def admin(client, method, path, **kwargs):
    if not client.cookies.get(auth.COOKIE):
        assert client.post("/ai/api/admin/login", json={"username": "ubuntu", "password": "test-password"}).status_code == 200
    return client.request(method, "/ai/api/admin/" + path, **kwargs)


def room(client):
    value = admin(client, "POST", "rooms", json={"title": "試験会議"}).json()
    value["host"] = {"Authorization": "Bearer " + value["host_url"].split("#host=")[1]}
    value["guest"] = {"Authorization": "Bearer " + value["guest_url"].split("#join=")[1]}
    return value


def test_administration_requires_authentication(client):
    page = client.get("/ai/admin", follow_redirects=False)
    assert page.status_code == 303 and page.headers["location"] == "/ai/login"
    assert "WWW-Authenticate" not in page.headers
    assert 'id="login-form"' in client.get("/ai/login").text
    assert client.get("/ai/api/admin/settings").status_code == 401
    # 古いBasic認証情報がブラウザーに残っていても、セッションなしではアクセスできない。
    assert client.get("/ai/api/admin/settings", auth=("ubuntu", "test-password")).status_code == 401
    assert admin(client, "GET", "settings").status_code == 200
    assert 'id="sign-out"' in client.get("/ai/admin").text


@pytest.mark.parametrize("username,password", [("ubuntu", "incorrect"), ("another", "test-password")])
def test_invalid_admin_credentials_never_create_a_session(client, username, password):
    response = client.post("/ai/api/admin/login", json={"username": username, "password": password})
    assert response.status_code == 401
    assert "WWW-Authenticate" not in response.headers
    assert "Set-Cookie" not in response.headers
    with store.db() as conn:
        assert conn.execute("SELECT COUNT(*) FROM admin_sessions").fetchone()[0] == 0


def test_admin_logout_revokes_cookie_replay_and_preserves_meetings_and_key(client):
    value = room(client)
    key = "sk-only-a-test-never-a-real-key"
    admin(client, "PUT", "settings", json={"api_key": key})
    token = client.cookies.get(auth.COOKIE)
    with store.db() as conn:
        assert conn.execute("SELECT hash FROM admin_sessions").fetchone()[0] != token
    response = client.post("/ai/api/admin/logout")
    assert response.json() == {"signed_out": True}
    assert "Max-Age=0" in response.headers["set-cookie"]
    assert not client.cookies.get(auth.COOKIE)
    assert client.get("/ai/api/admin/settings", headers={"Cookie": f"{auth.COOKIE}={token}"}).status_code == 401
    assert client.get("/ai/api/admin/settings", auth=("ubuntu", "test-password")).status_code == 401
    assert client.get("/ai/admin", follow_redirects=False).headers["location"] == "/ai/login"
    assert store.settings()["api_key"] == key
    assert client.get(f"/ai/api/rooms/{value['id']}", headers=value["guest"]).status_code == 200
    assert client.post("/ai/api/admin/logout").json() == {"signed_out": True}
    assert admin(client, "GET", "settings").status_code == 200
    assert client.cookies.get(auth.COOKIE) != token


def test_admin_cookie_has_secure_flags_and_expires_after_eight_hours(client):
    response = client.post("/ai/api/admin/login", json={"username": "ubuntu", "password": "test-password"})
    cookie = response.headers["set-cookie"]
    assert all(flag in cookie for flag in ["HttpOnly", "Secure", "SameSite=strict", "Path=/ai", "Max-Age=28800"])
    assert "test-password" not in response.text + cookie
    with store.db() as conn:
        conn.execute("UPDATE admin_sessions SET expires=?", (time.time() - 1,))
    assert client.get("/ai/api/admin/settings").status_code == 401
    assert client.get("/ai/admin", follow_redirects=False).status_code == 303


def test_cross_site_sign_in_and_sign_out_are_rejected(client):
    assert admin(client, "GET", "settings").status_code == 200
    token = client.cookies.get(auth.COOKIE)
    for path in ["login", "logout"]:
        response = client.post("/ai/api/admin/" + path,
                               json={"username": "ubuntu", "password": "test-password"},
                               headers={"Origin": "https://attacker.invalid"})
        assert response.status_code == 403
    assert client.cookies.get(auth.COOKIE) == token
    assert client.get("/ai/api/admin/settings").status_code == 200


def test_admin_sign_out_keeps_other_browser_session_valid(client):
    admin(client, "GET", "settings")
    with TestClient(app, base_url=store.ORIGIN) as other:
        other.headers["Origin"] = store.ORIGIN
        admin(other, "GET", "settings")
        assert client.cookies.get(auth.COOKIE) != other.cookies.get(auth.COOKIE)
        client.post("/ai/api/admin/logout")
        assert client.get("/ai/api/admin/settings").status_code == 401
        assert other.get("/ai/api/admin/settings").status_code == 200


def test_admin_sign_in_rotates_session_and_survives_service_initialization(client):
    admin(client, "GET", "settings")
    previous = client.cookies.get(auth.COOKIE)
    client.post("/ai/api/admin/login", json={"username": "ubuntu", "password": "test-password"})
    assert client.cookies.get(auth.COOKIE) != previous
    assert client.get("/ai/api/admin/settings", headers={"Cookie": f"{auth.COOKIE}={previous}"}).status_code == 401
    store.init()
    assert client.get("/ai/api/admin/settings").status_code == 200


def test_changing_admin_password_invalidates_issued_sessions(client):
    admin(client, "GET", "settings")
    store.save_settings({**store.settings(), "admin_digest": hashlib.pbkdf2_hmac(
        "sha256", b"replacement-test-password", b"test-salt", 600000).hex()})
    assert client.get("/ai/api/admin/settings").status_code == 401


def test_saved_key_never_returned(client):
    key = "sk-only-a-test-never-a-real-key"
    result = admin(client, "PUT", "settings", json={"api_key": key})
    assert result.status_code == 200
    assert key not in result.text
    public = client.get("/ai/api/status").json()
    private = admin(client, "GET", "settings").json()
    assert public["configured"] and private["configured"]
    assert "api_key" not in private
    assert "admin_digest" not in private
    # Empty input preserves a configured key; deletion requires the separate explicit endpoint.
    admin(client, "PUT", "settings", json={"api_key": ""})
    assert store.settings()["api_key"] == key


def test_cross_site_writes_rejected(client):
    result = admin(client, "PUT", "settings", json={"audio_minutes": 480}, headers={"Origin": "https://attacker.invalid"})
    assert result.status_code == 403
    assert store.settings()["audio_minutes"] == 120


def test_model_catalog_separates_live_file_and_interpreter_models():
    catalog=model_catalog([{"id":name} for name in [
        "gpt-transcribe", "gpt-live-transcribe", "gpt-4o-transcribe-diarize", "whisper-1",
        "gpt-realtime-translate", "gpt-realtime-2.1", "gpt-5-mini", "gpt-image-1", "text-embedding-3-small",
        "gpt-4o-2024-05-13", "gpt-5-chat-latest", "o1-preview", "o1-mini", "o3-pro"]])
    ids=lambda key:{value["id"] for value in catalog[key]}
    assert "gpt-live-transcribe" in ids("transcription_model")
    assert "gpt-live-transcribe" not in ids("recording_transcription_model")
    assert "gpt-4o-transcribe-diarize" not in ids("transcription_model")
    assert ids("translation_model")=={"gpt-realtime-translate"}
    assert ids("minutes_model")=={"gpt-5-mini","o3-pro"}
    assert "o3-pro" not in ids("subtitle_model")


def test_model_candidates_require_admin_and_use_account_catalog(client,monkeypatch):
    async def models(*args,**kwargs):return {"data":[{"id":"gpt-transcribe"},{"id":"gpt-5-mini"}]}
    monkeypatch.setattr(ai,"request",models)
    assert client.get('/ai/api/admin/models').status_code==401
    result=admin(client,'GET','models')
    assert result.status_code==200
    assert result.json()['models']['transcription_model'][0]['id']=='gpt-transcribe'


def test_capabilities_are_room_scoped_and_roles_enforced(client):
    first, second = room(client), room(client)
    assert client.get(f"/ai/api/rooms/{first['id']}", headers=first["guest"]).status_code == 200
    assert client.get(f"/ai/api/rooms/{second['id']}", headers=first["guest"]).status_code == 403
    assert client.post(f"/ai/api/rooms/{first['id']}/close", headers=first["guest"]).status_code == 403
    assert client.post(f"/ai/api/rooms/{first['id']}/invite", headers=first["host"]).status_code == 200
    with store.db() as conn:
        conn.execute("UPDATE rooms SET expires=? WHERE id=?", (time.time()-1, first["id"]))
    assert client.get(f"/ai/api/rooms/{first['id']}", headers=first["host"]).status_code == 403


def test_live_job_repeated_click_is_idempotent(client):
    value = room(client)
    store.segment(value["id"], "Alex", "en", "source", "I will prepare the draft by Friday.")
    path = f"/ai/api/rooms/{value['id']}/minutes"
    first = client.post(path, headers=value["host"])
    second = client.post(path, headers=value["host"])
    assert first.status_code == second.status_code == 200
    assert first.json()["job"] == second.json()["job"]


def test_recording_discovery_requires_successful_finalization(client):
    value = room(client)
    folder = store.RECORDINGS / "session-1"
    folder.mkdir()
    media = folder / ("ai-" + value["id"] + "_recording.mp4")
    media.write_bytes(b"synthetic-test-file")
    ai.discover_recordings()
    assert admin(client, "GET", "jobs").json() == []
    (folder / ".complete").touch()
    ai.discover_recordings()
    ai.discover_recordings()
    jobs = admin(client, "GET", "jobs").json()
    assert len(jobs) == 1 and jobs[0]["room"] == value["id"]
    assert jobs[0]["has_video"]
    assert "media" not in jobs[0]


def test_manual_minutes_still_runs_with_automatic_generation_disabled(client, monkeypatch):
    """Disabling automatic sends must preserve recording access and explicit, authorized generation."""
    value = room(client)
    store.save_settings({**store.settings(), "auto_minutes": False, "api_key": "sk-test-only"})
    folder = store.RECORDINGS / "manual-session"
    folder.mkdir()
    (folder / ("ai-" + value["id"] + "_recording.mp4")).write_bytes(b"synthetic-test-file")
    (folder / ".complete").touch()
    ai.discover_recordings()
    jobs = admin(client, "GET", "jobs").json()
    assert jobs[0]["status"] == "recorded"
    assert admin(client, "POST", f"jobs/{jobs[0]['id']}/retry").status_code == 200
    processed = []

    async def capture(job):
        processed.append(job["id"])

    async def finish_cycle(_):
        raise asyncio.CancelledError

    monkeypatch.setattr(ai, "process_job", capture)
    monkeypatch.setattr(ai.asyncio, "sleep", finish_cycle)
    with pytest.raises(asyncio.CancelledError):
        asyncio.run(ai.recording_worker())
    assert processed == [jobs[0]["id"]]


def test_recording_path_and_download_isolation(client, tmp_path):
    first, second = room(client), room(client)
    inside = store.RECORDINGS / "test.mp4"
    inside.write_bytes(b"recording")
    outside = tmp_path / "outside.mp4"
    outside.write_bytes(b"private")
    with pytest.raises(ValueError):
        store.recording_path(outside)
    with store.db() as conn:
        conn.execute("INSERT INTO jobs(id,media,room,status,created,updated) VALUES (?,?,?,?,?,?)",
                     ("job-1", str(inside), first["id"], "done", time.time(), time.time()))
    path = f"/ai/api/rooms/{first['id']}/jobs/job-1/download/video"
    assert client.get(path, headers=first["guest"]).status_code == 403
    assert client.get(path, headers=first["host"]).content == b"recording"
    assert client.get(f"/ai/api/rooms/{second['id']}/jobs/job-1/download/video", headers=second["host"]).status_code == 403


def test_failed_minutes_preserves_transcription_for_retry(client, monkeypatch):
    store.save_settings({**store.settings(), "api_key": "sk-only-a-test"})
    value = room(client)
    store.segment(value["id"], "Alex", "en", "source", "Update the manual.")
    with store.db() as conn:
        conn.execute("INSERT INTO jobs(id,room,status,created,updated) VALUES (?,?,?,?,?)",
                     ("job-2", value["id"], "pending", time.time(), time.time()))
        job = dict(conn.execute("SELECT * FROM jobs WHERE id='job-2'").fetchone())
    async def fail(*args):
        raise RuntimeError("Sensitive provider body must never reach the client")
    monkeypatch.setattr(ai, "create_minutes", fail)
    asyncio.run(ai.process_job(job))
    result = admin(client, "GET", "jobs").json()[0]
    assert result["status"] == "error"
    assert result["transcript"] == "Alex: Update the manual."
    assert "Sensitive" not in result["error"]


def test_csv_formula_cells_are_neutralized(client):
    notes={"title":"試験","summary":"概要","decisions":[],"discussion":[],"uncertainties":[],
           "action_items":[{"task":"=HYPERLINK(1)","owner":"未定","due":"未定","evidence":"根拠"}]}
    with store.db() as conn:
        conn.execute("INSERT INTO jobs(id,status,notes,created,updated) VALUES (?,?,?,?,?)",
                     ("csv-job", "done", json.dumps(notes), time.time(), time.time()))
    result=admin(client, "GET", "jobs/csv-job/download/tasks")
    assert result.status_code == 200
    assert "'=HYPERLINK(1)" in result.text


def test_native_registration_requires_private_token_and_registered_room(client):
    value=room(client)
    store.save_settings({**store.settings(),"bridge_token":"test-bridge-secret","api_key":"sk-test-only"})
    payload={"room":"ai-"+value["id"],"meeting_id":"native-session-1","participants":{"1234abcd":"佐藤"}}
    assert client.post('/internal/bridge/register',json=payload).status_code==403
    header={"Authorization":"Bearer test-bridge-secret"}
    assert client.post('/internal/bridge/register',json={**payload,"room":"ai-"+"0"*32},headers=header).status_code==403
    result=client.post('/internal/bridge/register',json=payload,headers=header)
    assert result.status_code==200
    assert bridge.allowed(bridge.session('native-session-1'))
    assert client.get('/ai/api/status').json()['transport']=='jitsi-bridge'


def test_native_new_session_replaces_old_lease_and_close_disables_ai(client):
    value=room(client)
    store.save_settings({**store.settings(),"bridge_token":"test-bridge-secret","api_key":"sk-test-only"})
    header={"Authorization":"Bearer test-bridge-secret"}
    for number in [1,2]:
        assert client.post('/internal/bridge/register',json={"room":"ai-"+value['id'],"meeting_id":f"native-session-{number}"},headers=header).status_code==200
    assert not bridge.allowed(bridge.session('native-session-1'))
    assert bridge.allowed(bridge.session('native-session-2'))
    assert client.post(f"/ai/api/rooms/{value['id']}/close",headers=value['host']).status_code==200
    assert not bridge.allowed(bridge.session('native-session-2'))


def test_native_final_transcripts_are_deduplicated_and_named(client):
    value=room(client)
    store.save_settings({**store.settings(),"bridge_token":"test-bridge-secret","api_key":"sk-test-only"})
    client.post('/internal/bridge/register',json={"room":"ai-"+value['id'],"meeting_id":"native-session-1","participants":{"1234abcd":"佐藤"}},headers={"Authorization":"Bearer test-bridge-secret"})
    event={"event":"transcription-result","type":"transcription-result","is_interim":False,
           "message_id":"message-1","participant":{"id":"1234abcd-a0"},"language":"ja","transcript":[{"text":"金曜日までに原稿を作成します。"}]}
    for _ in range(2):bridge.save_result('native-session-1',bridge.session('native-session-1'),event)
    assert store.transcript(value['id'])=='佐藤: 金曜日までに原稿を作成します。'
    assert bridge.session('native-session-1')['transcripts']==1


def test_native_invitation_links_and_selectable_models_are_saved(client):
    value=room(client)
    assert value['conference_url'].endswith('/ai-'+value['id'])
    assert value['ios_url'].startswith('org.jitsi.meet://')
    assert 'scheme=org.jitsi.meet;package=org.jitsi.meet' in value['android_url']
    result=admin(client,'PUT','settings',json={"transcription_model":"gpt-live-transcribe",
        "recording_transcription_model":"gpt-transcribe","subtitle_model":"gpt-4.1-mini","minutes_model":"gpt-5-mini"})
    assert result.status_code==200
    assert store.settings()['transcription_model']=='gpt-live-transcribe'
    assert store.settings()['recording_transcription_model']=='gpt-transcribe'
    assert store.settings()['subtitle_model']=='gpt-4.1-mini'
