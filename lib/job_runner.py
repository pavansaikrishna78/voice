#!/usr/bin/env python
"""Runs one job with the existing pipeline. Started by bin/tts_job.sh, which holds the GPU lock.

  text front-end -> chunked synthesis -> QC + regenerate -> -16 LUFS -> WAV 24 kHz + MP3 192 kbps

Re-running a failed job continues after the last finished file, using the same seeds.
"""
import csv
import json
import shutil
import subprocess
import sys
import threading
import time
from pathlib import Path

import voicelib
from voicelib import HOME, copy_voice_to_refs, read_text_any

from jobctl import MODES, now, read_json, write_json

TEST_SECTIONS = 3
FIELDS = ["file", "heading", "characters", "chunks", "audio_seconds", "generation_seconds", "retried"]
# Starting guesses for the time estimate, from finished jobs on the Tesla T4. The synthesis
# rate is replaced by the job's own measured rate after its first chunk.
SYNTHESIS_SECONDS_PER_AUDIO_SECOND = 3.0
QC_SECONDS_PER_AUDIO_SECOND = 1.0


def time_left_text(seconds: float) -> str:
    minutes = round(seconds / 60)
    return "about 1 min left" if minutes <= 1 else f"about {minutes} min left"


class ChunkProgress:
    """Turns finished chunks into a status line such as 'chunk 3/26, about 12 min left'."""

    def __init__(self, status, estimates: list, clock=time.perf_counter):
        self.status, self.clock = status, clock
        self.total_chunks, self.total_audio = len(estimates), sum(estimates)
        self.done_chunks, self.done_audio = 0, 0.0
        self.started = clock()

    def remaining_seconds(self) -> float:
        if self.done_audio > 0:
            rate = (self.clock() - self.started) / self.done_audio
        else:
            rate = SYNTHESIS_SECONDS_PER_AUDIO_SECOND
        return (self.total_audio - self.done_audio) * rate + self.total_audio * QC_SECONDS_PER_AUDIO_SECOND

    def phase(self) -> str:
        return (f"generating audio, chunk {self.done_chunks}/{self.total_chunks}, "
                f"{time_left_text(self.remaining_seconds())}")

    def chunk_done(self, est_seconds: float) -> None:
        self.done_chunks += 1
        self.done_audio += est_seconds
        self.status.update(phase=self.phase())


class JobError(Exception):
    """A problem to show the user as one plain sentence."""


class Status:
    def __init__(self, job: Path):
        self.path = job / "status.json"

    def update(self, **fields) -> None:
        status = read_json(self.path)
        status.update(fields)
        write_json(self.path, status)


class VramPeak(threading.Thread):
    """Whole-GPU peak from nvidia-smi. torch alone cannot see Whisper's memory."""

    def __init__(self):
        super().__init__(daemon=True)
        self.peak_mib = 0
        self.stopped = threading.Event()

    def run(self):
        while not self.stopped.wait(2):
            try:
                out = subprocess.run(["nvidia-smi", "--query-gpu=memory.used", "--format=csv,noheader,nounits"],
                                     capture_output=True, text=True, timeout=10).stdout
                self.peak_mib = max(self.peak_mib, int(out.split()[0]))
            except (OSError, ValueError, IndexError, subprocess.SubprocessError):
                pass


def load_sections(job: Path, spec: dict) -> list:
    """Returns [(heading, text)] in speaking order."""
    from batch import natural_key
    from from_script import parse_script

    inputs = job / "in"
    if spec["mode"] == "script":
        candidates = sorted(p for p in inputs.iterdir() if p.is_file() and p.suffix.lower() in (".md", ".txt"))
        preferred = [p for p in candidates if p.name == "script.md"]
        if not candidates:
            raise JobError("No script file found in in/.")
        path = (preferred or candidates)[0]
        print(f"script: {path.name}", flush=True)
        sections, skipped = parse_script(read_text_any(path))
        for heading in skipped:
            print(f"WARNING: no [NARRATION] in this section, so it was skipped: {heading}", flush=True)
        if not sections:
            raise JobError("The script has no '### ' sections with [NARRATION]: text, so there is nothing to speak.")
        return sections
    files = sorted((p for p in inputs.iterdir() if p.is_file() and p.suffix.lower() == ".txt"), key=natural_key)
    if not files:
        raise JobError("No slide .txt files found in in/.")
    if len(files) == 1:
        # A lecture script saved as .txt: read only its narration, not its headings and notes.
        sections, skipped = parse_script(read_text_any(files[0]))
        if sections:
            print("This .txt file is a lecture script, so only its [NARRATION] text will be spoken.", flush=True)
            for heading in skipped:
                print(f"WARNING: no [NARRATION] in this section, so it was skipped: {heading}", flush=True)
            return sections
    return [(p.stem, read_text_any(p)) for p in files]


def prepare_voices(sections, voice: str, refs: Path) -> None:
    """Copies every voice the script needs, or stops before any GPU time is spent."""
    from tts_engine import voices_used

    needed = {voice}.union(*(voices_used(text, voice) for _, text in sections))
    missing = sorted(name for name in needed if not (voicelib.VOICES / name / "ref.wav").is_file())
    if missing:
        names = ", ".join(missing)
        raise JobError(f"This script uses [STYLE ...], but these recordings are missing: {names}. "
                       f"For each one, put a clip with that name (for example {missing[0]}.m4a) and a "
                       f".txt of its exact words in 01_voice_clips, then run 1_Add_My_Voice.")
    for name in sorted(needed):
        copy_voice_to_refs(name, refs)


def write_manifest(rows: list, out: Path) -> None:
    with (out / "manifest.csv").open("w", newline="", encoding="utf-8-sig") as handle:  # BOM: Excel shows Telugu
        writer = csv.DictWriter(handle, fieldnames=FIELDS, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)


def render_sections(sections, spec, job, status, refs) -> list:
    from textprep import Speech, TextPrep
    from tts_engine import Engine, plan_segments

    out, work = job / "out", job / "work"
    done_dir = work / "done"
    done_dir.mkdir(parents=True, exist_ok=True)
    unknown = job / "logs" / "unknown_terms.txt"
    unknown.touch()
    prep = TextPrep(lexicon_path=HOME / "lexicon.tsv", unknown_log=unknown)
    engine = None
    rows, total = [], len(sections)

    def finished(stem: str) -> bool:
        return ((done_dir / f"{stem}.json").exists() and (out / f"{stem}.wav").exists()
                and (out / f"{stem}.mp3").exists())

    estimates = []  # only what is left to make, so a resumed job gets a fair estimate
    for number, (_, text) in enumerate(sections, 1):
        if not finished(f"Voice_{number:03d}"):
            estimates += [s.est_seconds for s, _ in plan_segments(prep, text, spec["voice"]) if isinstance(s, Speech)]
    progress = ChunkProgress(status, estimates)

    for number, (heading, text) in enumerate(sections, 1):
        stem = f"Voice_{number:03d}"
        marker = done_dir / f"{stem}.json"
        if finished(stem):
            row = json.loads(marker.read_text(encoding="utf-8"))
            if not row.get("skipped"):
                rows.append(row)
            print(f"[{number}/{total}] {stem}: already finished", flush=True)
            status.update(progress=f"{number}/{total}", current=heading)
            continue
        if engine is None:  # a fully finished job never loads the model
            status.update(phase="loading the voice model")
            engine = Engine(refs_dir=refs, prep=prep)
            engine.voice(spec["voice"])
        status.update(phase=progress.phase(), current=heading)
        print(f"[{number}/{total}] {stem}: {heading}", flush=True)
        try:
            result = engine.render(text, out / f"{stem}.wav", voice=spec["voice"], seed=spec["seed"],
                                   chunk_root=work / "chunks", on_chunk=progress.chunk_done)
            row = {"file": f"{stem}.wav", "heading": heading, "characters": result.characters, "chunks": result.chunks,
                   "audio_seconds": round(result.audio_seconds, 2),
                   "generation_seconds": round(result.generation_seconds, 2), "retried": result.retried}
            rows.append(row)
            write_manifest(rows, out)
            print(f"  {row['audio_seconds']} s audio, {row['generation_seconds']} s generation, "
                  f"{row['chunks']} chunks, {row['retried']} retried", flush=True)
            marker.write_text(json.dumps(row, ensure_ascii=True), encoding="utf-8")
        except ValueError as error:  # nothing left to speak in this section
            print(f"  WARNING: skipped: {error}", flush=True)
        status.update(progress=f"{number}/{total}")
    if not rows:
        raise JobError("None of the sections had any text to speak.")
    write_manifest(rows, out)
    return rows


def run_quality_check(rows, job, refs, status) -> None:
    import qc
    import soundfile as sf

    audio_seconds = sum(row["audio_seconds"] for row in rows)
    status.update(phase=f"quality check, {time_left_text(audio_seconds * QC_SECONDS_PER_AUDIO_SECOND)}", current=None)
    out, work = job / "out", job / "work"
    report = out / "qc_report.csv"
    checked = qc.run_qc(work, refs, regen=True, report=report)
    # Rewrite with a BOM so Excel on Windows shows the Telugu correctly.
    report.write_text(report.read_text(encoding="utf-8"), encoding="utf-8-sig")
    for row in rows:  # regenerated chunks change a file's length
        row["audio_seconds"] = round(sf.info(out / row["file"]).duration, 2)
    write_manifest(rows, out)
    print(f"QC: {len(checked)} chunks checked, {sum(r['flagged'] for r in checked)} flagged, "
          f"{sum(r['kept'] == 'regen' for r in checked)} replaced", flush=True)


def verify_outputs(rows, out: Path) -> None:
    missing = [name for row in rows for name in (row["file"], row["file"].replace(".wav", ".mp3"))
               if not (out / name).is_file() or (out / name).stat().st_size == 0]
    missing += [name for name in ("manifest.csv", "qc_report.csv") if not (out / name).is_file()]
    if missing:
        raise JobError(f"Expected output files are missing: {', '.join(missing)}")


def main(job: Path) -> int:
    status = Status(job)
    peak = VramPeak()
    started_clock = time.perf_counter()
    status.update(state="running", started=now(), finished=None, error=None, phase="starting", progress="0/0")
    try:
        spec = read_json(job / "job.json")
        if spec.get("mode") not in MODES:
            raise JobError(f"Unknown mode: {spec.get('mode')}")
        spec.setdefault("seed", 42)
        sections = load_sections(job, spec)
        if spec.get("test_only"):
            sections = sections[:TEST_SECTIONS]
        print(f"voice={spec['voice']} mode={spec['mode']} test_only={spec.get('test_only')} "
              f"seed={spec['seed']} sections={len(sections)}", flush=True)
        status.update(progress=f"0/{len(sections)}")

        # Loaded only now, so a bad job fails before the slow imports.
        import run_part  # noqa: F401  replaces tts_engine.assemble with the exact-pause version
        refs = job / "work" / "refs"
        prepare_voices(sections, spec["voice"], refs)
        peak.start()

        rows = render_sections(sections, spec, job, status, refs)
        run_quality_check(rows, job, refs, status)
        out = job / "out"
        shutil.copy2(job / "logs" / "unknown_terms.txt", out / "unknown_terms.txt")
        verify_outputs(rows, out)

        audio = sum(r["audio_seconds"] for r in rows)
        generation = sum(r["generation_seconds"] for r in rows)
        metrics = {"files": len(rows), "audio_minutes": round(audio / 60, 2), "generation_seconds": round(generation, 1),
                   "rtf_synthesis": round(generation / audio, 2) if audio else None,
                   "wall_seconds": round(time.perf_counter() - started_clock, 1),
                   "rtf_wall": round((time.perf_counter() - started_clock) / audio, 2) if audio else None,
                   "peak_vram_mib": peak.peak_mib}
        status.update(state="done", finished=now(), phase="finished", current=None, metrics=metrics)
        print(f"DONE {json.dumps(metrics)}", flush=True)
        return 0
    except (JobError, SystemExit) as error:
        message = str(error) or "The job stopped."
    except Exception as error:  # the log keeps the traceback; the status keeps it readable
        import traceback
        traceback.print_exc()
        message = f"{type(error).__name__}: {error}"
    finally:
        peak.stopped.set()
    print(f"FAILED: {message}", flush=True)
    status.update(state="failed", finished=now(), error=message)
    return 1


if __name__ == "__main__":
    sys.exit(main(Path(sys.argv[1]).resolve()))
