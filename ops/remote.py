#!/usr/bin/env python3
# File: ops/remote.py
# Summary: Run maintenance commands against the test Jitsi host with a pinned SSH host key.
"""Credentials are read from the environment and never written to the checkout."""

import argparse
import base64
import hashlib
import os
from pathlib import Path
import shlex
import sys

import paramiko

class PinnedHost(paramiko.MissingHostKeyPolicy):
    """Reject a server unless its observed key matches the verified initial fingerprint."""

    def __init__(self, fingerprint):
        self.fingerprint = fingerprint.removeprefix("SHA256:").rstrip("=")

    def missing_host_key(self, client, hostname, key):
        actual = base64.b64encode(hashlib.sha256(key.asbytes()).digest()).decode().rstrip("=")
        if actual != self.fingerprint:
            raise paramiko.SSHException("The server host key has changed")


def main():
    """Upload an optional file, then execute exactly one privileged maintenance command."""
    parser = argparse.ArgumentParser()
    parser.add_argument("--command")
    parser.add_argument("--script", type=Path)
    parser.add_argument("--upload", type=Path)
    parser.add_argument("--destination")
    args = parser.parse_args()
    required = ["JITSI_SSH_HOST", "JITSI_SSH_FINGERPRINT", "JITSI_SSH_PASSWORD"]
    if any(not os.environ.get(key) for key in required):
        parser.error("Set JITSI_SSH_HOST, JITSI_SSH_FINGERPRINT and JITSI_SSH_PASSWORD before connecting")
    password = os.environ["JITSI_SSH_PASSWORD"]
    client = paramiko.SSHClient()
    client.set_missing_host_key_policy(PinnedHost(os.environ["JITSI_SSH_FINGERPRINT"]))
    client.connect(os.environ["JITSI_SSH_HOST"], username=os.environ.get("JITSI_SSH_USER", "ubuntu"), password=password, timeout=20,
                   look_for_keys=False, allow_agent=False)
    client.get_transport().set_keepalive(20)
    try:
        if args.upload:
            with client.open_sftp() as sftp:
                sftp.put(str(args.upload), args.destination)
                sftp.chmod(args.destination, 0o600)
        command = args.script.read_text(encoding="utf-8-sig") if args.script else args.command
        if command:
            # The script is an argument rather than stdin, leaving stdin solely for sudo authentication.
            stdin, stdout, stderr = client.exec_command("sudo -S -p '' bash -c " + shlex.quote(command))
            stdin.write(password + "\n")
            stdin.flush()
            channel = stdout.channel
            while not channel.exit_status_ready() or channel.recv_ready() or channel.recv_stderr_ready():
                if channel.recv_ready():
                    sys.stdout.buffer.write(channel.recv(65536))
                    sys.stdout.buffer.flush()
                if channel.recv_stderr_ready():
                    sys.stderr.buffer.write(channel.recv_stderr(65536))
                    sys.stderr.buffer.flush()
                import time
                time.sleep(0.03)
            sys.exit(channel.recv_exit_status())
    finally:
        client.close()


if __name__ == "__main__":
    main()
