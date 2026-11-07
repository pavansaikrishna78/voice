#!/usr/bin/env bash
# voice_add.sh <name> <audio_file> [transcript_file]
# Prepares a clip and saves it to voices/<name>/. Prints JSON on the last line. Safe to re-run.
set -u
. "$(dirname "$(readlink -f "$0")")/_common.sh"
if [ $# -lt 2 ]; then echo "usage: voice_add.sh <name> <audio_file> [transcript_file]" >&2; exit 2; fi
exec "$TTS_PY" "$LIB/voice_add.py" "$@"
