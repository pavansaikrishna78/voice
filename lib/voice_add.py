#!/usr/bin/env python
"""Add a voice to the library.

  voice_add.py <name> <audio> [transcript]         prepare a recording
  voice_add.py <name> <wav> <txt> --as-is [--unverified]
                                                   copy an already prepared pair unchanged

The last stdout line is ASCII JSON describing the result. Progress goes to stderr.
"""
import argparse
import difflib
import hashlib
import json
import re
import shutil
import sys
import tempfile
import time
from pathlib import Path

import voicelib
from voicelib import VOICES, gpu_lock, read_text_any

from jobctl import now, valid_voice_name, write_json

SUPPORTED = {".mp3", ".m4a", ".wav", ".ogg", ".opus"}
MIN_SECONDS, MAX_SECONDS, WINDOW_MIN = 8.0, 15.0, 10.0
TARGET_LUFS = -20.0
MIN_PAUSE_SECONDS = 0.2
MISMATCH_BELOW = 0.45  # supplied transcript vs Whisper; Telugu ASR is rough, so only a big gap counts
LOCK_WAIT_SECONDS = 20


def say(message: str) -> None:
    print(message, file=sys.stderr, flush=True)


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def cleanest_window(audio, rate: int):
    """Picks the 10-15 s stretch that starts and ends at pauses.

    Returns (start, end, at_pauses). Prefers no clipping, then little dead air, then a
    length near 12.5 s. Falls back to the quietest cut points if nothing fits.
    """
    import librosa
    import numpy as np

    spans = librosa.effects.split(audio, top_db=30, frame_length=1024, hop_length=256)
    merged = []
    for start, end in spans:
        if merged and start - merged[-1][1] < MIN_PAUSE_SECONDS * rate:
            merged[-1][1] = int(end)
        else:
            merged.append([int(start), int(end)])

    best, best_score = None, None
    for i, (first_start, _) in enumerate(merged):
        for _, last_end in merged[i:]:
            seconds = (last_end - first_start) / rate
            if seconds > MAX_SECONDS:
                break
            if seconds < WINDOW_MIN:
                continue
            piece = audio[first_start:last_end]
            clipped = int(np.sum(np.abs(piece) >= 0.999))
            speech = sum(min(e, last_end) - max(s, first_start) for s, e in merged
                         if s < last_end and e > first_start) / piece.size
            score = (-clipped, round(speech, 2), -abs(seconds - 12.5))
            if best_score is None or score > best_score:
                best, best_score = (first_start, last_end), score
    if best:
        return best[0], best[1], True

    # One long unbroken stretch: cut at the quietest 50 ms near 12.5 s.
    frame = rate // 20
    search = audio[int(WINDOW_MIN * rate): int(MAX_SECONDS * rate)]
    usable = search.size // frame * frame
    energy = np.square(search[:usable]).reshape(-1, frame).sum(axis=1)
    return 0, int(WINDOW_MIN * rate) + int(np.argmin(energy)) * frame, False


def make_transcriber(device: str):
    """qc.py's windowed transcriber, which avoids Whisper stopping early on Telugu."""
    from faster_whisper import WhisperModel
    from qc import Transcriber

    transcriber = Transcriber.__new__(Transcriber)
    compute = "float16" if device == "cuda" else "int8"
    transcriber.model = WhisperModel("large-v3", device=device, compute_type=compute)
    return transcriber


def transcribe(path: Path) -> str:
    """Uses the GPU if it is free. During a batch job it uses the CPU rather than wait for hours."""
    with gpu_lock(LOCK_WAIT_SECONDS) as held:
        if held:
            say("Transcribing on the GPU...")
            return make_transcriber("cuda")(path).strip()
    say("The GPU is busy with a job, so transcribing on the CPU. This takes a minute or two...")
    return make_transcriber("cpu")(path).strip()


def similarity(a: str, b: str) -> float:
    from qc import normalize_for_cer
    return difflib.SequenceMatcher(None, normalize_for_cer(a), normalize_for_cer(b), autojunk=False).ratio()


def align_supplied(supplied: str, heard: str):
    """Finds the run of supplied sentences that matches the kept window. Returns (text, score)."""
    sentences = [s.strip() for s in re.split(r"(?<=[.?!।॥])\s+", supplied.strip()) if s.strip()]
    best, best_score = "", 0.0
    for i in range(len(sentences)):
        for j in range(i, len(sentences)):
            candidate = " ".join(sentences[i: j + 1])
            score = similarity(candidate, heard)
            if score > best_score:
                best, best_score = candidate, score
    return best, best_score


def install(name: str, wav: Path, transcript: str, meta: dict, source: Path) -> Path:
    """Backs up any earlier version, then puts the new one in place."""
    folder = VOICES / name
    folder.mkdir(parents=True, exist_ok=True)
    stamp = time.strftime("%Y%m%d-%H%M%S")
    for item in ("ref.wav", "ref.txt", "meta.json"):
        if (folder / item).exists():
            shutil.copy2(folder / item, folder / f"{item}.{stamp}.bak")
    shutil.copy2(wav, folder / "ref.wav")
    (folder / "ref.txt").write_text(transcript.strip() + "\n", encoding="utf-8")
    kept_source = folder / f"source{source.suffix.lower()}"
    if source.resolve() != kept_source.resolve():
        shutil.copy2(source, kept_source)
    write_json(folder / "meta.json", meta)
    return folder


def archive_upload(*sources) -> None:
    """Uploads from Windows move to done/. Files anywhere else are left where they are."""
    incoming = VOICES / "_incoming"
    for source in sources:
        if source and source.parent.resolve() == incoming.resolve():
            done = incoming / "done"
            done.mkdir(parents=True, exist_ok=True)
            shutil.move(str(source), done / source.name)


def measure(path: Path):
    import pyloudnorm as pyln
    import soundfile as sf
    audio, rate = sf.read(path, dtype="float32")
    return round(audio.size / rate, 2), round(float(pyln.Meter(rate).integrated_loudness(audio)), 1)


def finish(name, folder, meta, warnings, transcript, unchanged=False) -> int:
    print(json.dumps({"ok": True, "name": name, "folder": str(folder), "unchanged": unchanged,
                      "duration_seconds": meta["duration_seconds"], "lufs": meta["lufs"],
                      "transcript_auto": meta["transcript_auto"], "transcript_source": meta["transcript_source"],
                      "transcript": transcript.strip(), "warnings": warnings}, ensure_ascii=True))
    return 0


def add_as_is(args) -> int:
    """Keeps the audio byte for byte, so a voice sounds the same after moving to the library."""
    text = read_text_any(args.transcript)
    duration, lufs = measure(args.audio)
    auto = args.unverified
    meta = {"created": now(), "duration_seconds": duration, "lufs": lufs, "source_file": str(args.audio),
            "source_sha256": sha256(args.audio), "transcript_auto": auto,
            "transcript_source": "migrated-unverified" if auto else "migrated", "as_is": True, "warnings": []}
    previous = VOICES / args.name / "meta.json"
    if previous.exists() and (VOICES / args.name / "ref.wav").exists():
        old = json.loads(previous.read_text(encoding="utf-8"))
        if old.get("source_sha256") == meta["source_sha256"] and (VOICES / args.name / "ref.txt").read_text(encoding="utf-8").strip() == text.strip():
            return finish(args.name, VOICES / args.name, old, old.get("warnings", []), text, unchanged=True)
    folder = install(args.name, args.audio, text, meta, args.audio)
    return finish(args.name, folder, meta, [], text)


def add_recording(args) -> int:
    import numpy as np
    import soundfile as sf
    from prep_ref import SAMPLE_RATE, decode
    from tts_engine import normalize_loudness

    supplied = read_text_any(args.transcript).strip() if args.transcript else None
    if args.transcript and not supplied:
        raise SystemExit(f"The transcript file {args.transcript.name} is empty.")

    source_sha = sha256(args.audio)
    transcript_sha = hashlib.sha256(supplied.encode()).hexdigest() if supplied else None
    previous = VOICES / args.name / "meta.json"
    if previous.exists() and (VOICES / args.name / "ref.wav").exists():
        old = json.loads(previous.read_text(encoding="utf-8"))
        if old.get("source_sha256") == source_sha and old.get("transcript_sha256") == transcript_sha:
            say("This clip and transcript are already in the library. Nothing changed.")
            archive_upload(args.audio, args.transcript)
            return finish(args.name, VOICES / args.name, old, old.get("warnings", []),
                          (VOICES / args.name / "ref.txt").read_text(encoding="utf-8"), unchanged=True)

    warnings = []
    with tempfile.TemporaryDirectory() as tmp:
        tmp = Path(tmp)
        raw_path, filtered_path = tmp / "raw.wav", tmp / "filtered.wav"
        try:
            decode(args.audio, raw_path, highpass=False)
            decode(args.audio, filtered_path, highpass=True)
        except Exception:
            raise SystemExit(f"Could not read {args.audio.name} as audio. The file may be damaged.")
        raw, _ = sf.read(raw_path, dtype="float32")
        audio, _ = sf.read(filtered_path, dtype="float32")

        clipped = int(np.sum(np.abs(raw) >= 0.999))
        if clipped > 3:
            warnings.append(f"The recording clips ({clipped} samples at full scale). Record again a little quieter.")

        import librosa
        audio, _ = librosa.effects.trim(audio, top_db=35, frame_length=1024, hop_length=256)
        seconds = audio.size / SAMPLE_RATE
        if seconds < 1.0:
            raise SystemExit("The recording is silent or under 1 second after trimming. Record it again.")
        if seconds < MIN_SECONDS:
            warnings.append(f"The clip is only {seconds:.1f} s after trimming. Aim for 10-15 s.")

        was_cut = False
        if seconds > MAX_SECONDS:
            start, end, at_pauses = cleanest_window(audio, SAMPLE_RATE)
            audio = audio[start:end]
            was_cut = True
            kept = audio.size / SAMPLE_RATE
            warnings.append(f"The clip was {seconds:.1f} s, so the cleanest {kept:.1f} s ({start / SAMPLE_RATE:.1f}-{end / SAMPLE_RATE:.1f} s) was kept.")
            if not at_pauses:
                warnings.append("No clear pause was found to cut at, so the clip may end mid-word. A clip with pauses works better.")

        audio = normalize_loudness(audio.astype(np.float32), target=TARGET_LUFS)
        wav = tmp / "ref.wav"
        sf.write(wav, audio, SAMPLE_RATE, subtype="PCM_16")

        heard = transcribe(wav)
        if not heard and not supplied:
            raise SystemExit("Whisper could not hear any speech in the clip. Record it again, or add a transcript file.")

        if supplied and not was_cut:
            transcript, source, auto = supplied, "supplied", False
            score = similarity(supplied, heard)
            if score < MISMATCH_BELOW:
                warnings.append(f"The transcript does not seem to match the recording (match {score:.2f}). "
                                f"Whisper heard: {heard}")
        elif supplied:
            aligned, score = align_supplied(supplied, heard)
            if score >= MISMATCH_BELOW:
                transcript, source, auto = aligned, "aligned", True
                warnings.append("The clip was cut, so ref.txt keeps only the matching sentences of your transcript. Check them.")
            else:
                transcript, source, auto = heard, "whisper", True
                warnings.append("The clip was cut and your transcript could not be matched to it, so Whisper's words were used.")
        else:
            transcript, source, auto = heard, "whisper", True
        if auto:
            warnings.append("CHECK THE TRANSCRIPT: it was made automatically and Telugu speech recognition makes mistakes. "
                            "A wrong transcript is the most common cause of bad voice output.")

        duration, lufs = measure(wav)
        meta = {"created": now(), "duration_seconds": duration, "lufs": lufs, "source_file": args.audio.name,
                "source_sha256": source_sha, "transcript_sha256": transcript_sha, "transcript_auto": auto,
                "transcript_source": source, "was_cut": was_cut, "warnings": warnings}
        folder = install(args.name, wav, transcript, meta, args.audio)

    archive_upload(args.audio, args.transcript)
    return finish(args.name, folder, meta, warnings, transcript)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("name")
    parser.add_argument("audio", type=Path)
    parser.add_argument("transcript", type=Path, nargs="?")
    parser.add_argument("--as-is", action="store_true")
    parser.add_argument("--unverified", action="store_true", help="with --as-is: flag the transcript for checking")
    args = parser.parse_args()

    if not valid_voice_name(args.name):
        raise SystemExit(f"'{args.name}' cannot be a voice name. Use letters and numbers only (no spaces or dots).")
    if not args.audio.is_file():
        raise SystemExit(f"Audio file not found: {args.audio}")
    if args.audio.suffix.lower() not in SUPPORTED:
        raise SystemExit(f"{args.audio.name}: unsupported type. Use one of: {', '.join(sorted(SUPPORTED))}")
    if args.transcript and not args.transcript.is_file():
        raise SystemExit(f"Transcript file not found: {args.transcript}")
    if args.as_is and not args.transcript:
        raise SystemExit("--as-is needs a transcript file.")
    return add_as_is(args) if args.as_is else add_recording(args)


if __name__ == "__main__":
    sys.exit(main())
