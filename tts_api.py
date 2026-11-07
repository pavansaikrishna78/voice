#!/usr/bin/env python
"""Live Telugu speech on 127.0.0.1:8000 only. Reach it through an SSH tunnel.

  POST /speak {"text": "...", "voice": "pavan"}  -> MP3
  GET  /voices
  GET  /health

The model stays loaded. Each request takes the same GPU lock as the batch jobs, so nothing
overlaps; if a batch job is running, the request gets a clear "busy" answer.
"""
import shutil
import sys
import tempfile
import threading
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent / "lib"))
import voicelib
from voicelib import HOME, VOICES, copy_voice_to_refs, gpu_lock

import uvicorn
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse, Response
from pydantic import BaseModel

import jobctl

HOST = "127.0.0.1"  # never change this to 0.0.0.0
PORT = 8000
MAX_CHARACTERS = 3000
LOCK_WAIT_SECONDS = 30
DEFAULT_SEED = 42
LIVE_DIR = HOME / "live"
ALLOWED_HOSTS = {f"127.0.0.1:{PORT}", f"localhost:{PORT}"}

app = FastAPI(title="Telugu TTS live")
engine = None
load_error = None
loaded_voices = {}  # voice name -> ref.wav mtime, to notice when a voice is re-added
render_guard = threading.Lock()


class SpeakRequest(BaseModel):
    text: str
    voice: str = "pavan"


@app.middleware("http")
async def only_local_clients(request: Request, call_next):
    # A web page open on the PC could otherwise reach this through the tunnel.
    if request.headers.get("host") not in ALLOWED_HOSTS or request.headers.get("origin"):
        return JSONResponse({"detail": "Request refused."}, status_code=403)
    return await call_next(request)


def load_model() -> None:
    """Waits for any batch job to finish, so loading never overlaps GPU work."""
    global engine, load_error
    try:
        import run_part  # noqa: F401  replaces tts_engine.assemble with the exact-pause version
        from textprep import TextPrep
        from tts_engine import Engine

        (LIVE_DIR / "refs").mkdir(parents=True, exist_ok=True)
        prep = TextPrep(lexicon_path=HOME / "lexicon.tsv", unknown_log=LIVE_DIR / "unknown_terms.txt")
        while True:
            with gpu_lock(60) as held:
                if held:
                    engine = Engine(refs_dir=LIVE_DIR / "refs", prep=prep)
                    print("Model loaded.", flush=True)
                    return
            print("Waiting for the GPU before loading the model...", flush=True)
    except Exception as error:  # /health reports it
        load_error = f"{type(error).__name__}: {error}"
        print(f"Model failed to load: {load_error}", flush=True)
        raise


def refresh_voice(voice: str) -> None:
    mtime = (VOICES / voice / "ref.wav").stat().st_mtime
    if loaded_voices.get(voice) != mtime:
        copy_voice_to_refs(voice, LIVE_DIR / "refs")
        engine._voices.pop(voice, None)
        loaded_voices[voice] = mtime


@app.on_event("startup")
def start_loading() -> None:
    threading.Thread(target=load_model, daemon=True).start()


@app.get("/health")
def health():
    with gpu_lock(0) as free:
        return {"status": "ok" if engine else ("error" if load_error else "loading"),
                "model_loaded": engine is not None, "gpu_busy": not free, "error": load_error}


@app.get("/voices")
def voices():
    rows = []
    for name in jobctl.voice_names():
        try:
            rows.append({"name": name, **jobctl.read_json(VOICES / name / "meta.json")})
        except (OSError, ValueError):
            rows.append({"name": name})
    return rows


@app.post("/speak")
def speak(request: SpeakRequest):
    text = request.text.strip()
    if not text:
        raise HTTPException(422, "The text is empty.")
    if len(text) > MAX_CHARACTERS:
        raise HTTPException(413, f"The text is {len(text)} characters. The live limit is {MAX_CHARACTERS}; use a batch job for longer text.")
    if request.voice not in jobctl.voice_names():
        raise HTTPException(404, f"Voice '{request.voice}' not found. Available voices: {', '.join(jobctl.voice_names()) or 'none yet'}.")
    if engine is None:
        raise HTTPException(503, load_error or "The model is still loading. Try again in a minute.")

    work = Path(tempfile.mkdtemp(prefix="speak_"))
    try:
        with render_guard, gpu_lock(LOCK_WAIT_SECONDS) as held:
            if not held:
                raise HTTPException(503, "The GPU is busy with a batch job. Try again when it finishes.")
            refresh_voice(request.voice)
            started = time.perf_counter()
            from tts_engine import STYLE_TAG
            try:  # live mode has only the plain voice, so style tags are ignored here
                result = engine.render(STYLE_TAG.sub("", text), work / "speak.wav", voice=request.voice, seed=DEFAULT_SEED,
                                       chunk_root=work / "chunks")
            except ValueError as error:
                raise HTTPException(422, str(error))
        if not result.mp3 or not result.mp3.exists():
            raise HTTPException(500, "The audio could not be converted to MP3.")
        return Response(result.mp3.read_bytes(), media_type="audio/mpeg",
                        headers={"X-Audio-Seconds": f"{result.audio_seconds:.2f}",
                                 "X-Generation-Seconds": f"{time.perf_counter() - started:.2f}"})
    finally:
        shutil.rmtree(work, ignore_errors=True)


if __name__ == "__main__":
    uvicorn.run(app, host=HOST, port=PORT, log_level="info")
