#!/usr/bin/env python3
# File: ops/apply-japanese.py
# Summary: Fill missing Japanese Jitsi strings and correct the on-premises recording-retention description.

import json
from pathlib import Path
import shutil


def main():
    target = Path("/usr/share/jitsi-meet/lang/main-ja.json")
    backup = Path("/var/backups/jitsi-ai/main-ja.before-ai.json")
    backup.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    if not backup.exists():
        shutil.copyfile(target, backup)
    current = json.loads(target.read_text(encoding="utf-8"))
    overrides = json.loads(Path(__file__).with_name("ja-overrides.json").read_text(encoding="utf-8"))
    count = 0
    for key, value in overrides.items():
        if key.startswith("_"):
            continue
        parts = key.split(".")
        parent = current
        for part in parts[:-1]:
            parent = parent.setdefault(part, {})
        if parts[-1] not in parent or key == "recording.serviceDescriptionCloudInfo":
            parent[parts[-1]] = value
            count += 1
    target.write_text(json.dumps(current, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    target.chmod(0o644)
    print("JAPANESE_STRINGS_APPLIED", count)


if __name__ == "__main__":
    main()
