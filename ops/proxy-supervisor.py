#!/usr/bin/env python3
# File: ops/proxy-supervisor.py
# Summary: Apply private model/key settings to the official Opus proxy without writing API keys into deployment files.

import json
import os
from pathlib import Path
import signal
import subprocess
import time

DATA = Path("/var/lib/jitsi-ai")
stop = False


def environment():
    """Only the service process receives the key. Web/mobile participants receive RTP and caption messages."""
    config = json.loads((DATA / "settings.json").read_text())
    key = config.get("api_key", "")
    return {
        "HOST": "127.0.0.1", "PORT": "8766", "OPUS_BACKEND": "native",
        "OPENAI_API_KEY": key, "OPENAI_MODEL": config.get("transcription_model", "gpt-transcribe"),
        "OPENAI_TRANSLATION_API_KEY": key,
        "OPENAI_TRANSLATION_MODEL": config.get("translation_model", "gpt-realtime-translate"),
        "PROVIDERS_PRIORITY": "openai", "ENABLE_TEXT_TRANSLATION": "true",
        "TEXT_TRANSLATION_PROVIDERS_PRIORITY": "openai", "TEXT_TRANSLATION_OPENAI_API_KEY": key,
        "TEXT_TRANSLATION_OPENAI_MODEL": config.get("subtitle_model", "gpt-4o-mini"),
        "DUMP_BASE_PATH": str(DATA / "proxy-cache"), "DUMP_TRANSCRIPTS": "false",
        "DUMP_WEBSOCKET_MESSAGES": "false", "LOG_LEVEL": os.environ.get("JITSI_PROXY_LOG_LEVEL", "warn"), "DEBUG": "false",
        "ENABLE_OPENAI_CUSTOM_PROVIDER": "false", "SESSION_RESUME_ENABLED": "false",
        "TRANSLATE_TRANSCRIPTS": "true", "ENABLE_TRANSCRIBE": "true", "ENABLE_TRANSLATE": "true",
    }


def terminate(process):
    if process and process.poll() is None:
        os.killpg(process.pid, signal.SIGTERM)
        try:
            process.wait(timeout=10)
        except subprocess.TimeoutExpired:
            os.killpg(process.pid, signal.SIGKILL)
            process.wait()


def shutdown(signum, frame):
    global stop
    stop = True


def main():
    """Restart only when relevant settings change or the proxy exits, with no privileged restart permission."""
    signal.signal(signal.SIGTERM, shutdown)
    signal.signal(signal.SIGINT, shutdown)
    process, current = None, None
    try:
        while not stop:
            selected = environment()
            if selected != current or (process and process.poll() is not None):
                terminate(process)
                process = None
                if selected["OPENAI_API_KEY"]:
                    process = subprocess.Popen(
                        ["/opt/jitsi-node/bin/node", "dist/bundle/server.js"], cwd="/opt/jitsi-opus-proxy",
                        env={**os.environ, **selected, "PATH": "/opt/jitsi-node/bin:/usr/local/bin:/usr/bin:/bin"},
                        start_new_session=True)
                current = selected
            time.sleep(2)
    finally:
        terminate(process)


if __name__ == "__main__":
    main()
