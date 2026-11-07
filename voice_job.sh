#!/usr/bin/env bash
# Server side of the Windows automation. Each job lives in its own folder, so jobs
# never overwrite each other or /home/pavan/voice/output.
#
#   voice_job.sh new                  print a new job id and create its folder
#   voice_job.sh start  <job-id>      detach and render <job>/input.md; returns at once
#   voice_job.sh status <job-id>      print queued | running | done | failed
#   voice_job.sh log    <job-id>      print the last 40 log lines
#
# Jobs run one at a time on the GPU; later ones wait in the queue.
set -u

VOICE=/home/pavan/voice
PY=/home/pavan/telugu-tts/.venv/bin/python
JOBS=$VOICE/jobs
ALL_SECTIONS=1000000

cmd=${1:-}
id=${2:-}

if [ "$cmd" = new ]; then
    id=$(date +%Y%m%d-%H%M%S)-$RANDOM
    mkdir -p "$JOBS/$id" && echo "$id"
    exit
fi

[[ "$id" =~ ^[0-9]{8}-[0-9]{6}-[0-9]+$ ]] || { echo "bad job id: $id" >&2; exit 2; }
job=$JOBS/$id
[ -d "$job" ] || { echo "no such job: $id" >&2; exit 2; }

case "$cmd" in
start)
    [ -s "$job/input.md" ] || { echo "missing or empty input.md" >&2; exit 2; }
    echo queued > "$job/status"
    setsid "$0" _run "$id" > "$job/job.log" 2>&1 < /dev/null &
    echo $! > "$job/pid"
    ;;
_run)
    exec 9> "$JOBS/.gpu.lock"
    flock 9
    echo running > "$job/status"
    "$PY" "$VOICE/run_part.py" --script "$job/input.md" --limit "$ALL_SECTIONS" -o "$job/output"
    code=$?
    if [ "$code" -eq 0 ] && [ -s "$job/output/report.csv" ]; then
        echo done > "$job/status"
    else
        echo "run_part.py exited with code $code"
        echo failed > "$job/status"
    fi
    ;;
status)
    state=$(cat "$job/status" 2>/dev/null || echo failed)
    # A reboot or kill leaves the state stuck, so check the process is still alive.
    if { [ "$state" = queued ] || [ "$state" = running ]; } && ! kill -0 "$(cat "$job/pid" 2>/dev/null)" 2>/dev/null; then
        state=failed
    fi
    echo "$state"
    ;;
log)
    tail -n 40 "$job/job.log"
    ;;
*)
    echo "usage: voice_job.sh new | start|status|log <job-id>" >&2
    exit 2
    ;;
esac
