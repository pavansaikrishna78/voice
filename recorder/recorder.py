#!/usr/bin/env python3
"""Browser voice recorder, served on localhost only.

  python3 recorder.py [--port 7861] [--out /home/pavan/voice/recordings]

Open it through an SSH tunnel. Browsers allow the microphone on http://localhost.
Each saved take becomes a new file, and nothing existing is overwritten.
"""
import argparse
import html
import json
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
MAX_BYTES = 50 * 1024 * 1024

PAGE = r"""<!doctype html>
<html lang="te"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Reference Voice Recorder</title>
<style>
:root { --bg:#f3f6f7; --panel:#fff; --fg:#17262b; --muted:#566a71; --line:#d3dee1; --accent:#0b6e75; --accent-fg:#fff; --rec:#b3261e; --ok:#1b6e3c; --warn:#955a00; color-scheme:light }
@media (prefers-color-scheme:dark) { :root { --bg:#10191c; --panel:#18252a; --fg:#e7f0f2; --muted:#9db0b6; --line:#2d3f45; --accent:#5cc4cb; --accent-fg:#06282b; --rec:#ff8a80; --ok:#7bd8a0; --warn:#f0bd66; color-scheme:dark } }
* { box-sizing:border-box }
body { margin:0; background:var(--bg); color:var(--fg); font:16px/1.5 system-ui,"Segoe UI",Roboto,sans-serif; padding-block:24px }
main { max-width:720px; margin:auto; padding-inline:16px; display:grid; gap:16px }
h1 { font-size:1.35rem; margin:0 }
.panel { background:var(--panel); border:1px solid var(--line); border-radius:8px; padding:20px; display:grid; gap:14px }
.label { font-size:.8rem; color:var(--muted); font-weight:600 }
#sentence { font:1.45rem/1.85 "Noto Sans Telugu","Nirmala UI","Gautami",sans-serif; margin:0 }
.row { display:flex; flex-wrap:wrap; gap:10px; align-items:center }
button { font:inherit; font-weight:600; padding:10px 18px; border-radius:6px; border:1px solid var(--line); background:transparent; color:var(--fg); cursor:pointer }
button:focus-visible { outline:3px solid var(--accent); outline-offset:2px }
button:disabled { opacity:.45; cursor:not-allowed }
button.primary { background:var(--accent); border-color:var(--accent); color:var(--accent-fg) }
button.rec { background:var(--rec); border-color:var(--rec); color:var(--panel) }
#time { font:600 2rem/1 ui-monospace,Consolas,monospace; font-variant-numeric:tabular-nums }
.meter { height:12px; background:var(--line); border-radius:6px; overflow:hidden }
#level { height:100%; width:0; background:var(--accent) }
#level.hot { background:var(--rec) }
audio { width:100%; }
ul { margin:0; padding-left:20px }
.ok { color:var(--ok) } .warn { color:var(--warn) } .err { color:var(--rec) }
.path { font-family:ui-monospace,Consolas,monospace; overflow-wrap:anywhere }
</style></head><body><main>
<h1>Reference voice recorder</h1>
<section class="panel">
  <div class="label">READ THIS EXACTLY, IN YOUR NORMAL TEACHING VOICE</div>
  <p id="sentence">__SENTENCE__</p>
</section>
<section class="panel">
  <div class="row"><span id="time">0.0 s</span><span class="label">Target: 10 to 15 seconds</span></div>
  <div class="meter" aria-hidden="true"><div id="level"></div></div>
  <div class="row">
    <button id="record" class="primary">Start recording</button>
    <button id="stop" disabled>Stop</button>
  </div>
  <div id="status" role="status">Use a quiet room, with no fan or music. Your browser will ask for microphone access.</div>
</section>
<section class="panel" id="review" hidden>
  <div class="label">LISTEN TO THIS TAKE</div>
  <audio id="player" controls></audio>
  <ul id="checks"></ul>
  <div class="row"><button id="save" class="primary">Save this take</button><span>or press Start recording to try again.</span></div>
  <div id="saved" role="status"></div>
</section>
</main>
<script>
const $ = id => document.getElementById(id);
let stream, ctx, source, node, chunks = [], started = 0, timer, wav = null;

function encodeWav(samples, rate) {
  const buf = new ArrayBuffer(44 + samples.length * 2), v = new DataView(buf);
  const text = (o, s) => [...s].forEach((c, i) => v.setUint8(o + i, c.charCodeAt(0)));
  text(0, "RIFF"); v.setUint32(4, 36 + samples.length * 2, true); text(8, "WAVEfmt ");
  v.setUint32(16, 16, true); v.setUint16(20, 1, true); v.setUint16(22, 1, true);
  v.setUint32(24, rate, true); v.setUint32(28, rate * 2, true); v.setUint16(32, 2, true);
  v.setUint16(34, 16, true); text(36, "data"); v.setUint32(40, samples.length * 2, true);
  samples.forEach((s, i) => { const x = Math.max(-1, Math.min(1, s)); v.setInt16(44 + i * 2, x < 0 ? x * 32768 : x * 32767, true); });
  return new Blob([buf], { type: "audio/wav" });
}

function line(text, cls) { const li = document.createElement("li"); li.textContent = text; li.className = cls; $("checks").append(li); }

$("record").onclick = async () => {
  $("review").hidden = true; $("saved").textContent = ""; chunks = []; wav = null;
  try {
    stream = await navigator.mediaDevices.getUserMedia({ audio: { channelCount: 1, echoCancellation: false, noiseSuppression: false, autoGainControl: false } });
  } catch (e) {
    $("status").textContent = "The microphone is not available: " + e.message + ". Allow it in the browser's address bar, and open the page at http://localhost:7861.";
    $("status").className = "err"; return;
  }
  ctx = new AudioContext();
  source = ctx.createMediaStreamSource(stream);
  node = ctx.createScriptProcessor(4096, 1, 1);
  node.onaudioprocess = e => {
    const d = new Float32Array(e.inputBuffer.getChannelData(0)); chunks.push(d);
    let p = 0; for (const x of d) p = Math.max(p, Math.abs(x));
    $("level").style.width = Math.min(100, p * 100) + "%"; $("level").classList.toggle("hot", p > 0.95);
  };
  source.connect(node); node.connect(ctx.destination);
  started = performance.now();
  timer = setInterval(() => { $("time").textContent = ((performance.now() - started) / 1000).toFixed(1) + " s"; }, 100);
  $("record").disabled = true; $("stop").disabled = false; $("stop").classList.add("rec");
  $("status").textContent = "Recording. Read the sentence, then press Stop."; $("status").className = "";
};

$("stop").onclick = async () => {
  clearInterval(timer); node.disconnect(); source.disconnect(); stream.getTracks().forEach(t => t.stop());
  const rate = ctx.sampleRate; await ctx.close();
  const n = chunks.reduce((a, c) => a + c.length, 0), all = new Float32Array(n);
  let o = 0; for (const c of chunks) { all.set(c, o); o += c.length; }
  let peak = 0, clipped = 0, sum = 0;
  for (const x of all) { const a = Math.abs(x); peak = Math.max(peak, a); if (a >= 0.999) clipped++; sum += x * x; }
  wav = encodeWav(all, rate);
  $("player").src = URL.createObjectURL(wav);
  $("checks").replaceChildren();
  const secs = n / rate, db = peak > 0 ? 20 * Math.log10(peak) : -Infinity;
  if (secs < 10) line(secs.toFixed(1) + " s is under 10 s, before trimming. Read a little slower.", "warn");
  else if (secs > 17) line(secs.toFixed(1) + " s is long. Silence gets trimmed, but speech must end up under 15 s.", "warn");
  else line(secs.toFixed(1) + " s. Silence at the start and end gets trimmed later.", "ok");
  if (clipped > 0) line("The recording clips (" + clipped + " samples). Move back from the mic and record again.", "err");
  else if (db < -30) line("Very quiet (peak " + db.toFixed(0) + " dB). Move closer to the mic.", "warn");
  else line("Level is fine, with no clipping (peak " + db.toFixed(0) + " dB).", "ok");
  $("review").hidden = false; $("save").disabled = false;
  $("record").disabled = false; $("record").textContent = "Record again"; $("stop").disabled = true; $("stop").classList.remove("rec");
  $("level").style.width = "0"; $("status").textContent = "Listen before you save.";
};

$("save").onclick = async () => {
  $("save").disabled = true; $("saved").className = ""; $("saved").textContent = "Saving...";
  try {
    const r = await fetch("/save", { method: "POST", headers: { "Content-Type": "audio/wav" }, body: wav });
    const j = await r.json();
    if (!r.ok) throw new Error(j.error);
    $("saved").className = "ok path"; $("saved").textContent = "Saved to " + j.path + ". Tell Claude it is ready.";
  } catch (e) {
    $("save").disabled = false; $("saved").className = "err"; $("saved").textContent = "Could not save: " + e.message + " Check that the recorder and SSH tunnel are still running, then try again.";
  }
};
</script></body></html>
"""


class Handler(BaseHTTPRequestHandler):
    out_dir: Path
    port: int

    def _send(self, code, body, ctype):
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def _json(self, code, obj):
        self._send(code, json.dumps(obj).encode(), "application/json")

    def _host_ok(self):
        allowed = {f"localhost:{self.port}", f"127.0.0.1:{self.port}"}
        return self.headers.get("Host") in allowed  # blocks DNS rebinding

    def do_GET(self):
        if not self._host_ok():
            return self._json(403, {"error": "Open this page at http://localhost."})
        if self.path != "/":
            return self._json(404, {"error": "Not found."})
        sentence = (ROOT / "ref.txt").read_text(encoding="utf-8").strip()
        self._send(200, PAGE.replace("__SENTENCE__", html.escape(sentence)).encode(), "text/html; charset=utf-8")

    def do_POST(self):
        origin = self.headers.get("Origin")
        allowed = {f"http://localhost:{self.port}", f"http://127.0.0.1:{self.port}"}
        if not self._host_ok() or (origin is not None and origin not in allowed):
            return self._json(403, {"error": "Request refused."})
        if self.path != "/save":
            return self._json(404, {"error": "Not found."})
        try:
            length = int(self.headers.get("Content-Length", "0"))
        except ValueError:
            return self._json(400, {"error": "Bad request."})
        if not 44 < length <= MAX_BYTES:
            return self._json(413, {"error": "The recording is empty or too large."})
        data = self.rfile.read(length)
        if len(data) != length or data[:4] != b"RIFF" or data[8:12] != b"WAVE":
            return self._json(400, {"error": "That is not a WAV recording."})
        self.out_dir.mkdir(parents=True, exist_ok=True)
        stem = "take_" + time.strftime("%Y%m%d_%H%M%S")
        for i in range(1000):
            path = self.out_dir / (f"{stem}.wav" if i == 0 else f"{stem}_{i}.wav")
            try:
                with open(path, "xb") as f:  # x: never overwrites
                    f.write(data)
                return self._json(200, {"path": str(path)})
            except FileExistsError:
                continue
        return self._json(500, {"error": "Could not choose a file name."})

    def log_message(self, fmt, *args):
        print(time.strftime("%H:%M:%S"), fmt % args, flush=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--port", type=int, default=7861)
    parser.add_argument("--out", type=Path, default=ROOT / "recordings")
    args = parser.parse_args()
    Handler.out_dir, Handler.port = args.out, args.port
    print(f"Recorder on http://localhost:{args.port}, saving to {args.out}", flush=True)
    ThreadingHTTPServer(("127.0.0.1", args.port), Handler).serve_forever()


if __name__ == "__main__":
    main()
