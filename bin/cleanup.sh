#!/usr/bin/env bash
# cleanup.sh [--dry-run]
# Deletes only the WAV files in out/, only in jobs whose download Windows confirmed
# (.downloaded) at least 14 days ago. MP3s, inputs, logs and job folders are kept.
set -u
. "$(dirname "$(readlink -f "$0")")/_common.sh"
dry=false
if [ "${1:-}" = "--dry-run" ]; then dry=true
elif [ $# -gt 0 ]; then echo "usage: cleanup.sh [--dry-run]" >&2; exit 2; fi
if [ ! -d "$JOBS" ]; then echo "No jobs folder: $JOBS" >&2; exit 1; fi

jobs_seen=0; deleted=0
for job in "$JOBS"/*/; do
    job=${job%/}
    [ -L "$job" ] && continue
    [ -f "$job/.downloaded" ] || continue
    [ -n "$(find "$job/.downloaded" -maxdepth 0 -mtime +13 2>/dev/null)" ] || continue
    state=$(jq -r '.state // ""' "$job/status.json" 2>/dev/null)
    [ "$state" = done ] || continue
    jobs_seen=$((jobs_seen + 1))
    while IFS= read -r -d '' wav; do
        if $dry; then echo "would delete: $wav"
        else rm -f -- "$wav" && echo "deleted: $wav"; fi
        deleted=$((deleted + 1))
    done < <(find "$job/out" -maxdepth 1 -type f -name '*.wav' -print0 2>/dev/null)
done
echo "{\"jobs_checked\": $jobs_seen, \"wavs\": $deleted, \"dry_run\": $dry}"
