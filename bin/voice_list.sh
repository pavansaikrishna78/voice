#!/usr/bin/env bash
# voice_list.sh: prints the voice library as JSON.
set -u
. "$(dirname "$(readlink -f "$0")")/_common.sh"
jobctl voices
