#!/usr/bin/env bash
# tts_job.sh <job_dir>
# Starts a job in the background and returns at once. The job survives an SSH disconnect.
# Safe to re-run: a running or finished job is left alone, and a failed one continues.
set -u
. "$(dirname "$(readlink -f "$0")")/_common.sh"

if [ "${1:-}" = "--run" ]; then
    # Background half. FD 8 (.job.lock) was inherited from the parent, and keeps the job
    # marked as alive for as long as any process here holds it.
    job=$2
    exec 9>>"$GPU_LOCK"
    jobctl set "$job" phase="waiting for the GPU (another job is running)"
    flock 9                               # waits its turn; only one GPU job at a time
    "$TTS_PY" -u "$LIB/job_runner.py" "$job"
    code=$?
    if [ "$code" -ne 0 ] && [ "$(jq -r .state "$job/status.json")" != failed ]; then
        jobctl set "$job" state=failed "finished=$(date +%Y-%m-%dT%H:%M:%S%z)" "error=The job stopped with exit code $code. See job.log."
    fi
    exit "$code"
fi

if [ $# -ne 1 ]; then echo "usage: tts_job.sh <job_dir>" >&2; exit 2; fi
job=$(readlink -f "$1" 2>/dev/null || echo "$1")
if ! jobctl status "$job" >/dev/null; then exit 1; fi   # jobctl printed why

exec 8>>"$job/.job.lock"
if ! flock -n 8; then
    echo '{"started": false, "reason": "already running"}'
    exit 0
fi
if [ "$(jq -r .state "$job/status.json")" = done ]; then
    echo '{"started": false, "reason": "already finished"}'
    exit 0
fi
if ! problem=$(jobctl check_start "$job" 2>&1 >/dev/null); then
    jobctl set "$job" state=failed "finished=$(date +%Y-%m-%dT%H:%M:%S%z)" "error=$problem" launched=false
    echo "$problem" >> "$job/job.log"
    echo "$problem" >&2
    exit 1
fi
jobctl set "$job" state=queued launched=true started= finished= error= phase="waiting for the GPU"
echo "=== started $(date +%Y-%m-%dT%H:%M:%S%z) ===" >> "$job/job.log"
nohup setsid "$0" --run "$job" >> "$job/job.log" 2>&1 < /dev/null &
disown
echo '{"started": true}'
