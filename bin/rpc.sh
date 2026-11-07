#!/usr/bin/env bash
# rpc.sh <command> [base64 argument]...
# The one entry point Windows calls over SSH. Every argument is base64, so spaces and
# Telugu cannot be damaged by PowerShell, ssh or the remote shell on the way here.
set -u
. "$(dirname "$(readlink -f "$0")")/_common.sh"
if [ $# -lt 1 ]; then echo "usage: rpc.sh <command> [base64 args]" >&2; exit 2; fi
cmd=$1; shift
args=()
for encoded in "$@"; do
    if ! decoded=$(printf '%s' "$encoded" | base64 -d 2>/dev/null; printf x); then
        echo "Bad argument sent to the server." >&2; exit 2
    fi
    args+=("${decoded%x}")
done
case "$cmd" in
    ping)          printf '{"ok": true, "user": "%s"}\n' "$(id -un)" ;;
    voice_add)     exec "$BIN/voice_add.sh" "${args[@]}" ;;
    voice_list)    exec "$BIN/voice_list.sh" ;;
    job_create)    exec "$BIN/job_create.sh" "${args[@]}" ;;
    job_start)     exec "$BIN/tts_job.sh" "${args[@]}" ;;
    job_status)    exec "$BIN/job_status.sh" "${args[@]}" ;;
    job_list)      jobctl list "${args[@]}" ;;
    job_files)     jobctl files "${args[@]}" ;;
    job_log)       jobctl log "${args[@]}" ;;
    job_downloaded) jobctl mark_downloaded "${args[@]}" ;;
    job_last_test) jobctl last_test ;;
    job_clone_full) jobctl clone_full "${args[@]}" ;;
    *) echo "Unknown command: $cmd" >&2; exit 2 ;;
esac
