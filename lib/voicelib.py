"""Shared setup for code that uses the existing telugu-tts pipeline. Import this first."""
import fcntl
import os
import shutil
import sys
import time
from contextlib import contextmanager
from pathlib import Path

HOME = Path(os.environ.get("VOICE_HOME", "/home/pavan/voice"))
TTS = Path("/home/pavan/telugu-tts")
VOICES = Path(os.environ.get("VOICES_DIR", HOME / "voices"))
GPU_LOCK = Path("/home/pavan/voice/jobs/.gpu.lock")  # one lock for the whole machine, tests included

os.environ["HF_HOME"] = str(TTS / "hf-cache")
os.environ["HF_HUB_OFFLINE"] = "1"
os.environ["TRANSFORMERS_OFFLINE"] = "1"
for folder in (str(TTS), str(HOME), str(Path(__file__).resolve().parent)):
    if folder not in sys.path:
        sys.path.insert(0, folder)


@contextmanager
def gpu_lock(timeout: float):
    """Yields True if the GPU lock was taken within the timeout, else False."""
    GPU_LOCK.parent.mkdir(parents=True, exist_ok=True)
    with GPU_LOCK.open("a") as handle:
        deadline = time.monotonic() + timeout
        held = False
        while True:
            try:
                fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
                held = True
                break
            except BlockingIOError:
                if time.monotonic() >= deadline:
                    break
                time.sleep(0.25)
        try:
            yield held
        finally:
            if held:
                fcntl.flock(handle, fcntl.LOCK_UN)


def copy_voice_to_refs(voice: str, refs_dir: Path) -> None:
    """Engine and qc.py look for refs_dir/<name>.wav and .txt.

    A copy, not a link: re-adding a voice cannot change a job that is already running.
    """
    source = VOICES / voice
    if not ((source / "ref.wav").is_file() and (source / "ref.txt").is_file()):
        raise SystemExit(f"Voice '{voice}' not found in {VOICES}.")
    refs_dir.mkdir(parents=True, exist_ok=True)
    shutil.copy2(source / "ref.wav", refs_dir / f"{voice}.wav")
    shutil.copy2(source / "ref.txt", refs_dir / f"{voice}.txt")


def read_text_any(path: Path) -> str:
    """Windows editors save UTF-8, UTF-8 with BOM or UTF-16. Normalises line endings."""
    data = path.read_bytes()
    if data[:2] in (b"\xff\xfe", b"\xfe\xff"):
        text = data.decode("utf-16")
    else:
        try:
            text = data.decode("utf-8-sig")
        except UnicodeDecodeError:
            raise SystemExit(f"{path.name} is not UTF-8 text. In Notepad choose Save As, Encoding: UTF-8.")
    return text.replace("\r\n", "\n").replace("\r", "\n")
