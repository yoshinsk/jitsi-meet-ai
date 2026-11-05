#!/usr/bin/env python3
# File: ops/configure-recording.py
# Summary: Configure the test host's stable Jibri without printing or exporting its generated passwords.

import grp
import json
import os
from pathlib import Path
import re
import secrets
import subprocess

from domain import require_domain

DOMAIN = require_domain()


def replace_block(path, marker, content):
    """Maintain one tagged configuration block across repeat deployments."""
    text = path.read_text()
    text = re.sub(r"\n?# BEGIN " + re.escape(marker) + r"\n.*?# END " + re.escape(marker) + r"\n?",
                  "\n", text, flags=re.S)
    path.write_text(text.rstrip() + f"\n\n# BEGIN {marker}\n{content}\n# END {marker}\n")


def main():
    """Keep Jibri on localhost, constrain recording resolution, and connect its two XMPP identities."""
    os.umask(0o077)
    secret_path = Path("/etc/jitsi-ai/recorder-secrets.json")
    secret_path.parent.mkdir(exist_ok=True)
    if secret_path.exists():
        values = json.loads(secret_path.read_text())
    else:
        values = {key: secrets.token_urlsafe(32) for key in ["control", "recorder"]}
        secret_path.write_text(json.dumps(values))
    for username, domain, key in [("jibri", f"auth.{DOMAIN}", "control"),
                                  ("recorder", f"recorder.{DOMAIN}", "recorder")]:
        subprocess.run(["prosodyctl", "register", username, domain, values[key]], check=True,
                       stdout=subprocess.DEVNULL)
    config = f'''jibri {{
  id = "jitsi-recorder-1"
  recording {{
    recordings-directory = "/srv/jitsi-recordings"
    finalize-script = "/opt/jitsi-ai/ops/finalize-recording.sh"
  }}
  api.http {{
    external-api-host = "127.0.0.1"
    internal-api-host = "127.0.0.1"
  }}
  api.xmpp.environments = [{{
    name = "{DOMAIN}"
    xmpp-server-hosts = ["127.0.0.1"]
    xmpp-domain = "{DOMAIN}"
    control-login {{
      domain = "auth.{DOMAIN}"
      username = "jibri"
      password = "{values['control']}"
    }}
    control-muc {{
      domain = "internal.auth.{DOMAIN}"
      room-name = "JibriBrewery"
      nickname = "jitsi-recorder-1"
    }}
    call-login {{
      domain = "recorder.{DOMAIN}"
      username = "recorder"
      password = "{values['recorder']}"
    }}
    strip-from-room-domain = "conference."
    trust-all-xmpp-certs = true
    usage-timeout = 0
  }}]
  ffmpeg {{
    resolution = "1280x720"
    framerate = 15
    video-encode-preset-recording = "veryfast"
  }}
  screen {{
    xrandr-enabled = true
    default-resolution = "1280x720"
  }}
  stats.enable-stats-d = false
}}
'''
    path = Path("/etc/jitsi/jibri/jibri.conf")
    path.write_text(config)
    os.chown(path, 0, grp.getgrnam("jibri").gr_gid)
    path.chmod(0o640)
    replace_block(Path("/etc/jitsi/jicofo/jicofo.conf"), "JITSI AI RECORDER",
                  f'jicofo.jibri {{\n  brewery-jid = "JibriBrewery@internal.auth.{DOMAIN}"\n  pending-timeout = 90 seconds\n}}')
    web = Path(f"/etc/jitsi/meet/{DOMAIN}-config.js")
    text = web.read_text()
    text = re.sub(r"\n?// BEGIN JITSI AI.*?// END JITSI AI\n?", "\n", text, flags=re.S)
    text += f'''\n// BEGIN JITSI AI
// File: /etc/jitsi/meet/{DOMAIN}-config.js
// Summary: Default Japanese UI and one server recorder, maintained outside upstream packages.
config.defaultLanguage = 'ja';
config.hiddenDomain = 'recorder.{DOMAIN}';
config.recordingService = {{ enabled: true, sharingEnabled: false, hideStorageWarning: false }};
config.localRecording = {{ disable: false, notifyAllParticipants: true, disableSelfRecording: false }};
// END JITSI AI
'''
    web.write_text(text)
    web.chmod(0o644)
    prosody = Path(f"/etc/prosody/conf.avail/{DOMAIN}.cfg.lua")
    text = prosody.read_text()
    if "-- BEGIN JITSI AI HIDDEN" not in text:
        text = f'-- BEGIN JITSI AI HIDDEN\nmuc_hidden_domains = {{ "recorder.{DOMAIN}" }};\n-- END JITSI AI HIDDEN\n\n' + text
        prosody.write_text(text)
        prosody.chmod(0o644)
    xorg = Path("/etc/jitsi/jibri/xorg-video-dummy.conf")
    text = xorg.read_text().replace('    Modes "1920x1080"', '    Modes "1280x720" "1920x1080"')
    xorg.write_text(text)
    xorg.chmod(0o644)
    check = subprocess.run(["prosodyctl", "check", "config"])
    # Prosody returns 2 for advisory warnings in an otherwise loadable legacy configuration.
    # A parser/load failure is fatal; warnings remain visible in the maintenance output.
    if check.returncode not in (0, 2):
        raise RuntimeError("Prosody configuration failed to load")
    subprocess.run(["systemctl", "restart", "prosody", "jicofo"], check=True)
    subprocess.run(["systemctl", "enable", "--now", "jibri"], check=True)
    print("JIBRI_CONFIGURED")


if __name__ == "__main__":
    main()
