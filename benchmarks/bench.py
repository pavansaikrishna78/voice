#!/usr/bin/env python
"""Repeatable speed + quality benchmark. Changes no pipeline code; wraps it from outside.

  /home/pavan/telugu-tts/.venv/bin/python -u bench.py [--variants base,base2,...]

Takes the shared GPU lock per test, so real jobs can run between tests.
"""
import argparse, csv, json, shutil, subprocess, sys, threading, time, traceback
from pathlib import Path
T0 = time.perf_counter()
HERE = Path(__file__).resolve().parent
sys.path.insert(0, "/home/pavan/voice/lib")
import voicelib
from voicelib import gpu_lock, copy_voice_to_refs, HOME

SOURCE_JOB = HOME / "jobs/20261107-101215_part1-telugu-script"
QC_JOB = HOME / "jobs/20261107-115803_telugu"
VOICE, SEED, N_CHUNKS = "pavan", 42, 8


def log(*a):
    print(f"[{time.strftime('%H:%M:%S')}]", *a, flush=True)


class Vram(threading.Thread):
    def __init__(self):
        super().__init__(daemon=True); self.peak = 0; self.stop_flag = threading.Event()
    def run(self):
        while not self.stop_flag.wait(1):
            try:
                out = subprocess.run(["nvidia-smi", "--query-gpu=memory.used", "--format=csv,noheader,nounits"], capture_output=True, text=True, timeout=10).stdout
                self.peak = max(self.peak, int(out.split()[0]))
            except Exception:
                pass


def load_sample():
    segs = []
    for meta in sorted((SOURCE_JOB / "work/chunks").glob("*/meta.json")):
        segs += [s for s in json.loads(meta.read_text(encoding="utf-8"))["segments"] if s["type"] == "speech"]
    return [{"text": s["text"], "est": s["est_seconds"]} for s in segs[:N_CHUNKS]]


def timed(fn, bucket, key, sync):
    def inner(*a, **k):
        sync(); start = time.perf_counter(); out = fn(*a, **k); sync()
        bucket[key] = bucket.get(key, 0.0) + time.perf_counter() - start
        return out
    return inner


class Bench:
    def __init__(self, out):
        self.out = out; self.results = {}; self.stages = {}
        (out / "refs").mkdir(parents=True, exist_ok=True)
        copy_voice_to_refs(VOICE, out / "refs")

    def load(self):
        s = time.perf_counter()
        import run_part_shim  # noqa
        import torch
        import soundfile as sf
        from textprep import TextPrep
        from tts_engine import Engine
        self.stages["python_imports_s"] = time.perf_counter() - s
        self.torch, self.sf = torch, sf
        s = time.perf_counter()
        prep = TextPrep(lexicon_path=HOME / "lexicon.tsv", unknown_log=self.out / "unknown_terms.txt")
        script = (HOME / "input/Part1_Telugu_Script.md").read_text(encoding="utf-8")
        prep.prepare(script)
        self.stages["text_prep_full_script_s"] = time.perf_counter() - s
        s = time.perf_counter()
        self.engine = Engine(refs_dir=self.out / "refs", prep=prep)
        torch.cuda.synchronize()
        self.stages["model_load_s"] = time.perf_counter() - s
        s = time.perf_counter()
        self.ref_audio, self.ref_text = self.engine.voice(VOICE)
        self.stages["reference_prep_s"] = time.perf_counter() - s
        self.ema, self.voc = self.engine.model.ema_model, self.engine.model.vocoder
        self.core = getattr(self.ema, "_orig_mod", self.ema)
        self.voc_core = getattr(self.voc, "_orig_mod", self.voc)
        self.stages["ema_is_compiled_wrapper"] = self.core is not self.ema
        self.times = {}
        self.orig_sample, self.orig_decode = self.core.sample, self.voc_core.decode
        self.core.sample = timed(self.orig_sample, self.times, "sample_s", torch.cuda.synchronize)
        self.voc_core.decode = timed(self.orig_decode, self.times, "vocoder_s", torch.cuda.synchronize)

    # one chunk through the real production call, with nfe as the only change
    def seq_chunk(self, text, nfe):
        from tts_engine import tidy_chunk
        import numpy as np
        self.torch.manual_seed(SEED)
        s = time.perf_counter()
        wave, _, _ = self.engine._utils.infer_process(self.ref_audio, self.ref_text, self.engine.for_model(text), self.ema, self.voc,
            mel_spec_type="vocos", speed=self.engine.model.config.speed, device="cuda", nfe_step=nfe, show_info=lambda *_: None)
        gen = time.perf_counter() - s
        s = time.perf_counter()
        audio = tidy_chunk(np.asarray(wave, dtype=np.float32))
        self.times["tidy_s"] = self.times.get("tidy_s", 0) + time.perf_counter() - s
        return audio, gen

    def batch_chunks(self, items, nfe):
        """True batch through model.sample, same noise rule (seed per batch), mask for padding."""
        import numpy as np, torchaudio
        from f5_tts.infer.utils_infer import convert_char_to_pinyin, hop_length, target_rms, target_sample_rate
        from tts_engine import tidy_chunk
        torch = self.torch
        audio, sr = torchaudio.load(self.ref_audio)
        rms = torch.sqrt(torch.mean(torch.square(audio)))
        if rms < target_rms:
            audio = audio * target_rms / rms
        if sr != target_sample_rate:
            audio = torchaudio.transforms.Resample(sr, target_sample_rate)(audio)
        audio = audio.to("cuda")
        ref_text = self.ref_text if len(self.ref_text[-1].encode()) != 1 else self.ref_text + " "
        ref_len = audio.shape[-1] // hop_length
        texts = [self.engine.for_model(i["text"]) for i in items]
        durs = [ref_len + int(ref_len / len(ref_text.encode()) * len(t.encode())) for t in texts]
        with torch.inference_mode():
            mel = self.core.mel_spec(audio).permute(0, 2, 1).repeat(len(items), 1, 1)
            torch.manual_seed(SEED)
            out, _ = self.core.sample(cond=mel, text=convert_char_to_pinyin([ref_text + t for t in texts]),
                duration=torch.tensor(durs, device="cuda"), lens=torch.full((len(items),), ref_len, device="cuda"),
                steps=nfe, cfg_strength=2.0, sway_sampling_coef=-1.0)
            waves = []
            for i, dur in enumerate(durs):
                gen = out[i, ref_len:dur, :].to(torch.float32).unsqueeze(0).permute(0, 2, 1)
                w = self.voc_core.decode(gen)
                if rms < target_rms:
                    w = w * rms / target_rms
                waves.append(w.squeeze().cpu().numpy())
        s = time.perf_counter()
        tidied = [tidy_chunk(np.asarray(w, dtype=np.float32)) for w in waves]
        self.times["tidy_s"] = self.times.get("tidy_s", 0) + time.perf_counter() - s
        return tidied

    def finish(self, name, chunks, sample, wall, vram):
        """Write chunks + meta, assemble with the production code, record everything."""
        from tts_engine import is_anomalous, SAMPLE_RATE
        import run_part
        d = self.out / name / "chunks" / "Voice"; d.mkdir(parents=True, exist_ok=True)
        meta = []
        for i, (a, item) in enumerate(zip(chunks, sample), 1):
            f = f"chunk_{i:03d}.wav"; self.sf.write(d / f, a, SAMPLE_RATE, subtype="FLOAT")
            meta.append({"type": "speech", "file": f, "text": item["text"], "seed": SEED, "seconds": a.size / SAMPLE_RATE, "est_seconds": item["est"], "gen_seconds": 0, "retried": False, "anomaly": is_anomalous(a.size / SAMPLE_RATE, item["est"])})
        (d / "meta.json").write_text(json.dumps({"voice": VOICE, "wav": "", "segments": meta}, ensure_ascii=False), encoding="utf-8")
        s = time.perf_counter(); peak = self.torch.cuda.max_memory_allocated() / 2**30
        run_part.assemble(d, self.out / name / "Voice.wav")
        asm = time.perf_counter() - s
        audio = sum(m["seconds"] for m in meta)
        r = {"wall_s": round(wall, 1), "audio_s": round(audio, 1), "min_per_min": round(wall / audio, 2), "assemble_mp3_s": round(asm, 2),
             "peak_vram_mib": vram, "anomalies": sum(m["anomaly"] for m in meta), **{k: round(v, 1) for k, v in self.times.items()},
             "chunk_seconds": [round(m["seconds"], 1) for m in meta]}
        self.results[name] = r; log(name, json.dumps(r)); self.save()

    def run_variant(self, name, sample, nfe=32, batch=1, compiled=False):
        with gpu_lock(7200) as held:
            assert held
            self.times.clear(); self.torch.cuda.reset_peak_memory_stats()
            vram = Vram(); vram.start()
            saved = None
            try:
                if compiled:
                    saved = self.core.transformer
                    self.core.transformer = self.torch.compile(saved)
                s = time.perf_counter()
                if batch == 1:
                    chunks = []
                    for item in sample:
                        a, g = self.seq_chunk(item["text"], nfe); chunks.append(a); log(f"  {name}: {a.size/24000:.1f}s audio in {g:.1f}s")
                    order = sample
                else:
                    order = sorted(sample, key=lambda i: len(i["text"].encode()))  # similar lengths together
                    chunks = []
                    for k in range(0, len(order), batch):
                        part = order[k:k + batch]; b = time.perf_counter()
                        chunks += self.batch_chunks(part, nfe); log(f"  {name}: batch of {len(part)} in {time.perf_counter()-b:.1f}s")
                    back = {id(i): c for i, c in zip(order, chunks)}
                    chunks = [back[id(i)] for i in sample]; order = sample
                wall = time.perf_counter() - s
                vram.stop_flag.set()
                self.finish(name, chunks, order, wall, vram.peak)
            except Exception as e:
                traceback.print_exc(); self.results[name] = {"error": f"{type(e).__name__}: {e}"}; self.save()
                self.torch.cuda.empty_cache()
            finally:
                vram.stop_flag.set()
                if saved is not None:
                    self.core.transformer = saved
        time.sleep(3)  # lets a waiting real job take the lock

    def whisper_tests(self, names):
        """Scores every variant with the production QC recipe, then tests cheaper recipes."""
        import librosa, qc
        from faster_whisper import WhisperModel
        with gpu_lock(7200) as held:
            assert held
            s = time.perf_counter()
            model = WhisperModel("large-v3", device="cuda", compute_type="float16")
            self.stages["whisper_load_s"] = round(time.perf_counter() - s, 1)

            def trans(path, beam):
                audio, _ = librosa.load(path, sr=16000, mono=True); out = []
                for a, b in qc.speech_windows(audio):
                    segs, _ = model.transcribe(audio[a:b], language="te", beam_size=beam, condition_on_previous_text=False, vad_filter=False)
                    out += [x.text.strip() for x in segs]
                return " ".join(out)

            for name in names:
                d = self.out / name / "chunks/Voice"
                if not d.exists():
                    continue
                meta = json.loads((d / "meta.json").read_text(encoding="utf-8"))["segments"]
                cers = [round(qc.cer(m["text"], trans(d / m["file"], 5)), 4) for m in meta]
                self.results[name]["cer"] = cers; self.results[name]["cer_mean"] = round(sum(cers) / len(cers), 4)
                log(name, "CER", self.results[name]["cer_mean"], cers); self.save()

            # QC recipes on a real 26-chunk job, whose beam-5 scores are already on record
            rows = list(csv.DictReader((QC_JOB / "out/qc_report.csv").open(encoding="utf-8-sig")))
            cdir = QC_JOB / "work/chunks/Voice_001"
            meta = {m["file"]: m for m in json.loads((cdir / "meta.json").read_text(encoding="utf-8"))["segments"] if m["type"] == "speech"}
            audio_s = sum(m["seconds"] for m in meta.values())
            qcres = {"audio_s": round(audio_s, 1)}
            cache = {}
            for label, beam in (("beam5", 5), ("beam1", 1)):
                s = time.perf_counter(); cache[label] = {f: qc.cer(m["text"], trans(cdir / f, beam)) for f, m in meta.items()}
                wall = time.perf_counter() - s
                top = sorted(cache[label], key=cache[label].get, reverse=True)[:3]
                qcres[label] = {"wall_s": round(wall, 1), "s_per_audio_min": round(wall / audio_s * 60, 1), "worst3": top, "cer_mean": round(sum(cache[label].values()) / len(meta), 4)}
                log("QC", label, qcres[label])
            self.results["_qc"] = qcres; self.save()
            del model; self.torch.cuda.empty_cache()

    def save(self):
        (self.out / "results.json").write_text(json.dumps({"stages": self.stages, "results": self.results}, indent=1, ensure_ascii=False), encoding="utf-8")


def main():
    ap = argparse.ArgumentParser(); ap.add_argument("--variants", default="base,base2,nfe24,nfe16,batch2,batch4,batch8,compiled"); args = ap.parse_args()
    out = HERE / ("run_" + time.strftime("%Y%m%d-%H%M%S")); out.mkdir(parents=True)
    (HERE / "run_part_shim.py").write_text("import sys\nsys.path.insert(0,'/home/pavan/voice')\nimport run_part\n")
    sys.path.insert(0, str(HERE))
    bench = Bench(out); sample = load_sample()
    (out / "sample.json").write_text(json.dumps(sample, ensure_ascii=False, indent=1), encoding="utf-8")
    log("output", out, "| chunks", len(sample), "| est seconds", [round(s["est"]) for s in sample])
    with gpu_lock(7200) as held:
        assert held
        bench.load()
    log("stages", bench.stages); bench.save()
    spec = {"base": {}, "base2": {}, "nfe24": {"nfe": 24}, "nfe16": {"nfe": 16}, "batch2": {"batch": 2}, "batch4": {"batch": 4}, "batch8": {"batch": 8}, "compiled": {"compiled": True}}
    names = [n for n in args.variants.split(",") if n in spec]
    for name in names:
        log("=== start", name); bench.run_variant(name, sample, **spec[name])
    bench.whisper_tests(names)
    log("ALL DONE", out)


if __name__ == "__main__":
    main()
