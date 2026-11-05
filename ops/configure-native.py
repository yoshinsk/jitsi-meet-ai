#!/usr/bin/env python3
# File: ops/configure-native.py
# Summary: Connect stable Jicofo/JVB to the private native proxy while restricting paid AI to administrator-created rooms.

import grp
import json
import os
from pathlib import Path
import re
import secrets
import subprocess
import sys
import tarfile
import time
from domain import require_domain


def main():
    domain = require_domain()
    os.umask(0o077)
    backup = Path('/var/backups/jitsi-ai') / time.strftime('native-%Y%m%d-%H%M%S')
    backup.mkdir(parents=True, mode=0o700)
    with tarfile.open(backup / 'configs.tgz', 'w:gz') as archive:
        for path in ['/etc/jitsi', '/etc/prosody', '/etc/nginx', '/var/lib/jitsi-ai/settings.json']:
            archive.add(path)
    settings_path=Path('/var/lib/jitsi-ai/settings.json')
    config=json.loads(settings_path.read_text())
    token=config.setdefault('bridge_token',secrets.token_urlsafe(40))
    config.setdefault('subtitle_model','gpt-4o-mini')
    settings_path.write_text(json.dumps(config,ensure_ascii=False,indent=2))
    settings_path.chmod(0o600)
    secret_path=Path('/etc/prosody/jitsi-ai-bridge-token')
    secret_path.write_text(token)
    os.chown(secret_path,0,grp.getgrnam('prosody').gr_gid)
    secret_path.chmod(0o640)
    target=Path('/usr/share/jitsi-meet/prosody-plugins/mod_jitsi_ai_native.lua')
    target.write_text(Path('/opt/jitsi-ai/ops/mod_jitsi_ai_native.lua').read_text())
    target.chmod(0o644)
    prosody=Path(f'/etc/prosody/conf.avail/{domain}.cfg.lua')
    text=prosody.read_text()
    if '"jitsi_ai_native";' not in text:
        # Install on the main conference MUC only; the brewery and normal control hosts are unaffected.
        start=text.index(f'Component "conference.{domain}" "muc"')
        index=text.index('modules_enabled = {',start)+len('modules_enabled = {')
        text=text[:index]+'\n        "jitsi_ai_native";'+text[index:]
    if f'Component "audiotranslation.{domain}"' not in text:
        text+=f'\nComponent "audiotranslation.{domain}" "audio_translation_component"\n    muc_component = "conference.{domain}"\n    breakout_rooms_component = "breakout.{domain}"\n    audio_translation_max_subscriptions = 4\n'
    # Each component has its own admin scope; only Jicofo may receive the private translation request map.
    for component in ['metadata', 'audiotranslation']:
        pattern=rf'(Component "{component}\.{re.escape(domain)}"[^\n]*\n)(.*?)(?=\n(?:Component|VirtualHost) |\Z)'
        match=re.search(pattern,text,re.S)
        if match and not re.search(r'^\s*admins\s*=',match.group(2),re.M):
            text=text[:match.start(2)]+f'    admins = {{ "focus@auth.{domain}" }}\n'+text[match.start(2):]
    prosody.write_text(text)
    prosody.chmod(0o644)
    jicofo=Path('/etc/jitsi/jicofo/jicofo.conf')
    text=jicofo.read_text()
    text=re.sub(r'\n?# BEGIN JITSI AI NATIVE\n.*?# END JITSI AI NATIVE\n?','\n',text,flags=re.S)
    text+=f'''\n# BEGIN JITSI AI NATIVE
jicofo.transcription {{
  url-template = "ws://127.0.0.1:8765/internal/bridge/transcribe?sessionId={{{{MEETING_ID}}}}"
  http-headers {{ "Authorization" = "Bearer {token}" }}
}}
jicofo.translation {{
  url-template = "ws://127.0.0.1:8765/internal/bridge/translate?sessionId={{{{MEETING_ID}}}}"
  http-headers {{ "Authorization" = "Bearer {token}" }}
  mode = "per-source"
  max-languages-per-connect = 5
}}
# END JITSI AI NATIVE
'''
    jicofo.write_text(text)
    web=Path(f'/etc/jitsi/meet/{domain}-config.js')
    text=web.read_text()
    text=re.sub(r'\n?// BEGIN JITSI AI NATIVE\n.*?// END JITSI AI NATIVE\n?','\n',text,flags=re.S)
    text+='''\n// BEGIN JITSI AI NATIVE
// Summary: Use bridge audio and standard Jitsi controls on the web, Android, and iOS.
config.audioTranslation = { enabled: true, duckedVolume: 0.15 };
config.transcription = { enabled: true, useAppLanguage: false, autoTranscribeOnRecord: false };
config.p2p = { ...config.p2p, enabled: false };
// END JITSI AI NATIVE
'''
    web.write_text(text)
    web.chmod(0o644)
    subprocess.run(['install','-m','644','/opt/jitsi-ai/ops/jitsi-ai-proxy.service','/etc/systemd/system/jitsi-ai-proxy.service'],check=True)
    check=subprocess.run(['prosodyctl','check','config'])
    if check.returncode not in (0,2):raise RuntimeError('Prosody configuration failed')
    subprocess.run(['systemctl','daemon-reload'],check=True)
    subprocess.run(['systemctl','restart','jitsi-ai'],check=True)
    subprocess.run(['systemctl','enable','--now','jitsi-ai-proxy'],check=True)
    subprocess.run(['systemctl','restart','prosody','jicofo'],check=True)
    print('NATIVE_BRIDGE_CONFIGURED')


if __name__=='__main__':main()
