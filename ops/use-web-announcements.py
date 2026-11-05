#!/usr/bin/env python3
# File: ops/use-web-announcements.py
# Summary: Retire the shared announcement bot and restore standard native notifications while preserving Japanese web audio.

import json
import os
from pathlib import Path
import re
import subprocess
import tarfile
import time
from domain import require_domain

DOMAIN = require_domain()
ROOT = Path('/opt/jitsi-ai')
SETTINGS = Path('/var/lib/jitsi-ai/settings.json')
WEB = Path(f'/etc/jitsi/meet/{DOMAIN}-config.js')
NATIVE = WEB.with_name(f'{DOMAIN}-ai-config.js')
NGINX = Path(f'/etc/nginx/sites-available/{DOMAIN}.conf')
UNIT = Path('/etc/systemd/system/jitsi-ai-announcer.service')
TOKEN = Path('/etc/jitsi-ai-announcer-token')
LEGACY = [ROOT / 'ops' / name for name in [
    'announcer.py', 'announcer-browser.js', 'configure-announcer.py', 'jitsi-ai-announcer.service']]


def main():
    """Modify only files/keys introduced by the retired bot, with a private backup and nginx rollback on syntax failure."""
    os.umask(0o077)
    web_before, nginx_before = WEB.read_text(), NGINX.read_text()
    settings = json.loads(SETTINGS.read_text()) if SETTINGS.exists() else {}
    filtered = {key: value for key, value in settings.items() if key not in {'announcer_enabled', 'announcer_token'}}
    web_after = re.sub(r'\n?// BEGIN JAPANESE ANNOUNCEMENTS\n.*?// END JAPANESE ANNOUNCEMENTS\n?',
                       '\n', web_before, flags=re.S)
    nginx_after = re.sub(
        r'\n[ \t]*# Japanese AI room notification config\n[ \t]*if[^\n]+\n'
        rf'[ \t]*set \$config_js_location /etc/jitsi/meet/{re.escape(DOMAIN)}-ai-config\.js;\n[ \t]*}}',
        '', nginx_before)
    if '# Japanese AI room notification config' in nginx_after:
        raise RuntimeError('The retired nginx block has unexpected contents; configuration was not changed')
    changed = web_before != web_after or nginx_before != nginx_after
    retire = UNIT.exists() or TOKEN.exists() or NATIVE.exists() or any(path.exists() for path in LEGACY)
    if not changed and not retire and settings == filtered:
        print('WEB_ONLY_ANNOUNCEMENTS_ALREADY_CONFIGURED')
        return
    backup = Path('/var/backups/jitsi-ai') / time.strftime('web-announcements-%Y%m%d-%H%M%S')
    backup.mkdir(parents=True, mode=0o700)
    with tarfile.open(backup / 'configs.tgz', 'w:gz') as archive:
        for path in [WEB, NGINX, NATIVE, SETTINGS, UNIT, TOKEN, *LEGACY]:
            if path.exists():
                archive.add(path)
    if changed:
        WEB.write_text(web_after)
        NGINX.write_text(nginx_after)
        check = subprocess.run(['nginx', '-t'])
        if check.returncode:
            WEB.write_text(web_before)
            NGINX.write_text(nginx_before)
            raise RuntimeError('Nginx validation failed; original configuration was restored')
        subprocess.run(['systemctl', 'reload', 'nginx'], check=True)
    if UNIT.exists():
        subprocess.run(['systemctl', 'disable', '--now', 'jitsi-ai-announcer'], check=True)
        UNIT.unlink()
        subprocess.run(['systemctl', 'daemon-reload'], check=True)
    if settings != filtered:
        # Preserve the API key, authentication, model choices and permissions without exposing any values.
        owner = SETTINGS.stat()
        temporary = SETTINGS.with_name('web-announcements-settings.tmp')
        temporary.write_text(json.dumps(filtered, ensure_ascii=False, indent=2))
        os.chown(temporary, owner.st_uid, owner.st_gid)
        temporary.chmod(0o600)
        os.replace(temporary, SETTINGS)
        if json.loads(SETTINGS.read_text()) != filtered:
            raise RuntimeError('Settings preservation check failed')
    TOKEN.unlink(missing_ok=True)
    if NATIVE.exists():
        effective = subprocess.run(['nginx', '-T'], check=True, capture_output=True, text=True).stdout
        if str(NATIVE) not in effective and '// Native AI meeting sound policy.' in NATIVE.read_text():
            NATIVE.unlink()
    for path in LEGACY:
        path.unlink(missing_ok=True)
    WEB.chmod(0o644)
    print('WEB_ONLY_ANNOUNCEMENTS_CONFIGURED')
    print('OTHER_SETTINGS_PRESERVED')
    print('BACKUP', backup)


if __name__ == '__main__':
    main()
