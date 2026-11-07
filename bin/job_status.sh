#!/usr/bin/env bash
# job_status.sh <job_dir>: prints status.json. A job that died is reported as failed.
set -u
. "$(dirname "$(readlink -f "$0")")/_common.sh"
if [ $# -ne 1 ]; then echo "usage: job_status.sh <job_dir>" >&2; exit 2; fi
exec /usr/bin/python3 "$LIB/jobctl.py" status "$1"
