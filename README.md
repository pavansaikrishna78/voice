# voice

Telugu voice-over service built on IndicF5 voice cloning. Runs TTS jobs on a GPU
server and is driven over SSH from a Windows client.

- `bin/` – shell commands (create jobs, check status, add voices, RPC entry point `rpc.sh`)
- `lib/` – job runner, job control and voice-library code
- `tts_api.py`, `run_part.py`, `voice_job.sh` – synthesis entry points
- `windows_client/` – double-click `.bat` tools + `tts.ps1` (see `README_WINDOWS.txt`)
- `windows/voice.ps1` – unattended client; pass `-Server user@host`
- `recorder/` – small web recorder for reference clips
- `lexicon.tsv` – pronunciation overrides

Uses the venv from the `telugu-tts` repo. Voice reference recordings, models,
jobs and outputs are intentionally not included.
