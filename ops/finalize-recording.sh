#!/usr/bin/env bash
# File: ops/finalize-recording.sh
# Summary: Mark only successfully finalized Jibri recordings for the durable AI processing queue.
set -euo pipefail
directory=$(realpath -- "$1")
case "$directory" in /srv/jitsi-recordings/*) ;; *) exit 1 ;; esac
test -d "$directory"
touch "$directory/.complete"
chmod -R g+rX "$directory"
