# Sourced by the bin/ scripts. All paths are absolute so they work from any folder.
VOICE_HOME="${VOICE_HOME:-/home/pavan/voice}"
export VOICE_HOME
LIB="$VOICE_HOME/lib"
BIN="$VOICE_HOME/bin"
JOBS="$VOICE_HOME/jobs"
GPU_LOCK="/home/pavan/voice/jobs/.gpu.lock"
TTS_PY="/home/pavan/telugu-tts/.venv/bin/python"   # the venv the working pipeline uses
export PYTHONWARNINGS=ignore
export PYTHONPATH="$LIB${PYTHONPATH:+:$PYTHONPATH}"
jobctl() { /usr/bin/python3 "$LIB/jobctl.py" "$@"; }
