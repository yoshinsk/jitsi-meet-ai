#!/usr/bin/env python3
# File: ops/package.py
# Summary: Build a credential-free deployment archive with explicit Linux permissions from a Windows checkout.

from pathlib import Path
import tarfile


def permissions(item):
    """Avoid inheriting Windows' synthetic world-writable/executable modes on the Linux host."""
    if "__pycache__" in item.name:
        return None
    item.uid = item.gid = 0
    item.uname = item.gname = "root"
    item.mode = 0o755 if item.isdir() or item.name.endswith(".sh") else 0o644
    return item


if __name__ == "__main__":
    Path(".local").mkdir(exist_ok=True)
    with tarfile.open(".local/jitsi-ai.tgz", "w:gz") as archive:
        for name in ["service", "static", "assets", "ops", "tests", "requirements.txt", "requirements-dev.txt",
                     "LICENSE", "NOTICE", "THIRD_PARTY.md"]:
            archive.add(name, filter=permissions)
    print(".local/jitsi-ai.tgz")
