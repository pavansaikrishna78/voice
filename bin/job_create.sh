#!/usr/bin/env bash
# job_create.sh <name> <voice> <script|slides> <test_only:true|false> [seed]
# Makes the job folder and prints {"job_dir": ...}. Upload to in/, then run tts_job.sh.
set -u
. "$(dirname "$(readlink -f "$0")")/_common.sh"
exec /usr/bin/python3 "$LIB/jobctl.py" create "$@"
