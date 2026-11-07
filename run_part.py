#!/usr/bin/env python
"""Render script sections with the telugu-tts engine, using this folder's lexicon.

  /home/pavan/telugu-tts/.venv/bin/python run_part.py --limit 3

Files are named Voice_001.wav/.mp3 and so on. A [PAUSE Ns] is exactly N seconds of
silence, and 0.25 s of silence goes only between two consecutive speech chunks.
"""
import argparse
import csv
import difflib
import json
import os
import re
import sys
import time
from pathlib import Path

V = Path("/home/pavan/voice")
TTS = Path("/home/pavan/telugu-tts")
os.environ["HF_HOME"] = str(TTS / "hf-cache")
os.environ["HF_HUB_OFFLINE"] = "1"
os.environ["TRANSFORMERS_OFFLINE"] = "1"
sys.path.insert(0, str(TTS))

import numpy as np
import soundfile as sf
import torch

import tts_engine
from from_script import parse_script
from textprep import TextPrep
from tts_engine import CHUNK_GAP_SECONDS, SAMPLE_RATE, Engine, fade_edges, normalize_loudness, write_outputs


def assemble(chunk_dir, wav_path):
    meta = json.loads((chunk_dir / "meta.json").read_text(encoding="utf-8"))
    pieces, previous = [], None
    gap = np.zeros(int(CHUNK_GAP_SECONDS * SAMPLE_RATE), dtype=np.float32)
    for seg in meta["segments"]:
        if seg["type"] == "silence":
            pieces.append(np.zeros(int(seg["seconds"] * SAMPLE_RATE), dtype=np.float32))
        else:
            if previous == "speech":
                pieces.append(gap)
            audio, _ = sf.read(chunk_dir / seg["file"], dtype="float32")
            pieces.append(fade_edges(audio))
        previous = seg["type"]
    audio = normalize_loudness(np.concatenate(pieces))
    return write_outputs(audio, wav_path), audio.size / SAMPLE_RATE


tts_engine.assemble = assemble


def words(text):
    return re.sub(r"[^\w\s]", " ", text.replace("‌", "").replace("‍", "")).split()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--script", type=Path, default=V / "input/Part1_Telugu_Script.md")
    parser.add_argument("--limit", type=int, required=True)
    parser.add_argument("--refs-dir", type=Path, default=TTS / "refs")
    parser.add_argument("--voice", default="pavan")
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument("-o", "--output", type=Path, default=V / "output")
    args = parser.parse_args()

    sections, _ = parse_script(args.script.read_text(encoding="utf-8"))
    sections = sections[: args.limit]
    prep = TextPrep(lexicon_path=V / "lexicon.tsv", unknown_log=V / "logs/unknown_terms.txt")
    engine = Engine(refs_dir=args.refs_dir, prep=prep)
    engine.voice(args.voice)
    print(f"voice={args.voice} seed={args.seed} sections={len(sections)}", flush=True)

    start = time.perf_counter()
    rows = []
    for i, (heading, body) in enumerate(sections, 1):
        wav = args.output / f"Voice_{i:03d}.wav"
        print(f"{wav.name}: {heading}", flush=True)
        result = engine.render(body, wav, voice=args.voice, seed=args.seed)
        meta = json.loads((args.output / "chunks" / wav.stem / "meta.json").read_text(encoding="utf-8"))
        for n, seg in enumerate((s for s in meta["segments"] if s["type"] == "speech"), 1):
            rows.append({"file": wav.stem, "chunk": n, "chunk_file": seg["file"], "seconds": round(seg["seconds"], 2),
                         "estimate": round(seg["est_seconds"], 1), "ratio": round(seg["seconds"] / seg["est_seconds"], 2),
                         "gen_seconds": round(seg["gen_seconds"], 1), "retried": seg["retried"],
                         "anomaly": seg["anomaly"], "seed": seg["seed"], "text": seg["text"]})
        print(f"  {result.audio_seconds:.1f} s audio, {result.generation_seconds:.0f} s generation, "
              f"{result.chunks} chunks, {result.retried} retried", flush=True)
    wall = time.perf_counter() - start
    speech = sum(r["seconds"] for r in rows)
    generation = sum(r["gen_seconds"] for r in rows)
    print(f"speech {speech:.1f} s, generation {generation:.0f} s, RTF {generation / speech:.2f}, wall {wall:.0f} s")
    print(f"peak VRAM allocated {torch.cuda.max_memory_allocated() / 2**30:.2f} GiB, "
          f"reserved {torch.cuda.max_memory_reserved() / 2**30:.2f} GiB", flush=True)

    from faster_whisper import WhisperModel
    whisper = WhisperModel("large-v3", device="cuda", compute_type="float16")
    for r in rows:
        path = args.output / "chunks" / r["file"] / r["chunk_file"]
        segs, _ = whisper.transcribe(str(path), language="te", beam_size=5, condition_on_previous_text=False)
        heard = " ".join(s.text.strip() for s in segs)
        a, b = "".join(words(r["text"])), "".join(words(heard))
        r["char_match"] = round(difflib.SequenceMatcher(None, a, b).ratio(), 2)
        r["heard"] = heard
        print(f'{r["file"]} chunk {r["chunk"]}: {r["seconds"]} s (est {r["estimate"]}, ratio {r["ratio"]}), '
              f'retried={r["retried"]}, whisper match {r["char_match"]}\n  HEARD: {heard}', flush=True)

    report = args.output / "report.csv"
    with report.open("w", newline="", encoding="utf-8-sig") as f:
        writer = csv.DictWriter(f, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)
    print(f"report: {report}\nDONE", flush=True)


if __name__ == "__main__":
    main()
