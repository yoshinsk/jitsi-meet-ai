#!/usr/bin/env python3
# File: ops/install-japanese-announcements.py
# Summary: Back up bundled English web announcement files and install locally generated Japanese MP3/Opus replacements.

import json
from pathlib import Path
import shutil
import subprocess


def main():
    source = Path('/opt/jitsi-ai/assets/announcements')
    target = Path('/usr/share/jitsi-meet/sounds')
    backup = Path('/var/backups/jitsi-ai/english-announcements')
    script = json.loads(Path('/opt/jitsi-ai/ops/japanese-announcements.json').read_text())
    names = [name for name in script if not name.startswith('_')]
    missing = [name for name in names if not (source / (name + '.wav')).is_file()]
    if len(missing) == len(names):
        print('WEB_JAPANESE_ANNOUNCEMENTS_SKIPPED: generate WAV files before enabling Japanese web announcements')
        return
    if missing:
        raise RuntimeError('Incomplete announcement set: ' + ', '.join(missing))
    backup.mkdir(parents=True, exist_ok=True, mode=0o700)
    for name in script:
        if name.startswith('_'):
            continue
        for extension, codec, rate in [('mp3', 'libmp3lame', '96k'), ('opus', 'libopus', '48k')]:
            output = target / (name + '.' + extension)
            if output.exists() and not (backup / output.name).exists():
                shutil.copyfile(output, backup / output.name)
            subprocess.run(['ffmpeg', '-nostdin', '-v', 'error', '-y', '-i', str(source / (name + '.wav')),
                            '-ac', '1', '-ar', '48000', '-c:a', codec, '-b:a', rate, str(output)], check=True)
            output.chmod(0o644)
    print('JAPANESE_WEB_ANNOUNCEMENTS_INSTALLED')


if __name__ == '__main__':
    main()
