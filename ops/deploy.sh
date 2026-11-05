#!/usr/bin/env bash
# File: ops/deploy.sh
# Summary: Deploy a reviewed companion bundle while preserving secrets, recordings, and original Jitsi packages.
set -euo pipefail
cd /opt/jitsi-ai
# Validate an operator-supplied host before any configuration or ownership changes.
jitsi_domain=$(python3 -c "import sys; sys.path.insert(0, 'ops'); from domain import require_domain; print(require_domain())")
export JITSI_DOMAIN="$jitsi_domain"
rm -f service/realtime.py static/pcm-worklet.js ops/smoke-live.py
# Privileged service code must remain immutable to its unprivileged runtime user.
chown -R root:root service static ops
find service static ops -type d -exec chmod 755 {} +
find service static ops -type f -exec chmod 644 {} +
chmod 755 ops/*.sh
python3 ops/apply-japanese.py
python3 ops/install-japanese-announcements.py
python3 ops/use-web-announcements.py
if [ -f /etc/prosody/jitsi-ai-bridge-token ]; then
  install -m 644 ops/mod_jitsi_ai_native.lua /usr/share/jitsi-meet/prosody-plugins/mod_jitsi_ai_native.lua
  prosodyctl shell module reload jitsi_ai_native "conference.$jitsi_domain"
fi
if ! id jitsi-ai >/dev/null 2>&1; then
  useradd --system --user-group --home-dir /var/lib/jitsi-ai --shell /usr/sbin/nologin jitsi-ai
fi
usermod -aG jibri jitsi-ai
install -d -o jitsi-ai -g jitsi-ai -m 700 /var/lib/jitsi-ai
if [ ! -f /var/lib/jitsi-ai/settings.json ]; then
  install -o jitsi-ai -g jitsi-ai -m 600 /tmp/jitsi-ai-admin-seed.json /var/lib/jitsi-ai/settings.json
fi
rm -f /tmp/jitsi-ai-admin-seed.json
python3 -m venv /opt/jitsi-ai/.venv
/opt/jitsi-ai/.venv/bin/pip install -q -r requirements.txt
install -m 644 ops/jitsi-ai.service /etc/systemd/system/jitsi-ai.service
install -m 644 ops/nginx-ai.conf /etc/nginx/snippets/jitsi-ai.conf
chmod 755 ops/finalize-recording.sh
python3 - <<'PY'
import os
from pathlib import Path
domain=os.environ['JITSI_DOMAIN']
environment=Path('/etc/jitsi-ai/environment')
environment.parent.mkdir(parents=True,exist_ok=True,mode=0o700)
environment.write_text(f'JITSI_DOMAIN={domain}\nJITSI_AI_ORIGIN=https://{domain}\n')
environment.chmod(0o600)
path=Path(f'/etc/nginx/sites-available/{domain}.conf')
text=path.read_text()
directive='    include /etc/nginx/snippets/jitsi-ai.conf;'
if directive not in text:
    text=text.replace('    root /usr/share/jitsi-meet;', '    root /usr/share/jitsi-meet;\n'+directive,1)
    path.write_text(text)
PY
nginx -t
systemctl daemon-reload
systemctl enable --now jitsi-ai
systemctl restart jitsi-ai
if [ -f /etc/systemd/system/jitsi-ai-proxy.service ]; then
  install -m 644 ops/jitsi-ai-proxy.service /etc/systemd/system/jitsi-ai-proxy.service
  systemctl daemon-reload
  systemctl restart jitsi-ai-proxy
fi
for attempt in $(seq 1 20); do
  if curl -fsS http://127.0.0.1:8765/ai/api/status >/dev/null; then
    systemctl reload nginx
    echo 'COMPANION_READY'
    exit 0
  fi
  sleep 1
done
journalctl -u jitsi-ai -n 20 --no-pager
exit 1
