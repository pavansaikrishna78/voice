"""Job runner and client checks. No GPU needed.

  cd /home/pavan/voice && /home/pavan/telugu-tts/.venv/bin/python -m pytest tests/ -q
"""
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "lib"))

import job_runner  # noqa: E402
from job_runner import ChunkProgress, load_sections, time_left_text  # noqa: E402

SCRIPT = """# Title that must not be spoken
Purpose: notes that must not be spoken

### Test 1
[NARRATION]:
మొదటి వాక్యం.
[DURATION]: 0.5 min

### Test 2
[NARRATION]:
రెండవ వాక్యం.
[DURATION]: 0.5 min
"""


def make_job(tmp_path, files: dict) -> Path:
    (tmp_path / "in").mkdir()
    for name, text in files.items():
        (tmp_path / "in" / name).write_text(text, encoding="utf-8")
    return tmp_path


def test_script_saved_as_txt_speaks_only_narration(tmp_path):
    job = make_job(tmp_path, {"slide_0001.txt": SCRIPT})
    sections = load_sections(job, {"mode": "slides"})
    assert [heading for heading, _ in sections] == ["Test 1", "Test 2"]
    spoken = " ".join(text for _, text in sections)
    assert "Purpose" not in spoken and "Title" not in spoken and "DURATION" not in spoken
    assert "మొదటి" in spoken and "రెండవ" in spoken


def test_script_saved_as_txt_with_windows_bom_and_line_endings(tmp_path):
    (tmp_path / "in").mkdir()
    (tmp_path / "in" / "slide_0001.txt").write_bytes(SCRIPT.replace("\n", "\r\n").encode("utf-8-sig"))
    assert len(load_sections(tmp_path, {"mode": "slides"})) == 2


def test_plain_single_slide_is_still_read_in_full(tmp_path):
    job = make_job(tmp_path, {"slide_0001.txt": "మొదటి స్లైడ్ వాక్యం."})
    assert load_sections(job, {"mode": "slides"}) == [("slide_0001", "మొదటి స్లైడ్ వాక్యం.")]


def test_slide_folder_keeps_natural_order(tmp_path):
    job = make_job(tmp_path, {"slide_10.txt": "పది", "slide_2.txt": "రెండు"})
    assert [text for _, text in load_sections(job, {"mode": "slides"})] == ["రెండు", "పది"]


def test_md_script_mode_unchanged(tmp_path):
    job = make_job(tmp_path, {"script.md": SCRIPT})
    assert len(load_sections(job, {"mode": "script"})) == 2


class FakeStatus:
    def __init__(self):
        self.phases = []

    def update(self, **fields):
        self.phases.append(fields["phase"])


def test_time_left_text():
    assert time_left_text(10) == "about 1 min left"
    assert time_left_text(600) == "about 10 min left"


def test_progress_starts_from_the_recorded_rates():
    progress = ChunkProgress(FakeStatus(), [60.0, 60.0], clock=lambda: 0.0)
    expected = 120 * job_runner.SYNTHESIS_SECONDS_PER_AUDIO_SECOND + 120 * job_runner.QC_SECONDS_PER_AUDIO_SECOND
    assert progress.remaining_seconds() == pytest.approx(expected)
    assert progress.phase() == "generating audio, chunk 0/2, about 8 min left"


def test_progress_switches_to_the_jobs_measured_rate():
    now = [0.0]
    status = FakeStatus()
    progress = ChunkProgress(status, [60.0, 60.0], clock=lambda: now[0])
    now[0] = 120.0  # 2 s per audio second, faster than the starting guess
    progress.chunk_done(60.0)
    assert progress.remaining_seconds() == pytest.approx(60 * 2 + 120 * job_runner.QC_SECONDS_PER_AUDIO_SECOND)
    assert status.phases == ["generating audio, chunk 1/2, about 4 min left"]


def test_render_reports_each_speech_chunk_but_not_silence(tmp_path, monkeypatch):
    import numpy as np
    from textprep import Silence, Speech
    import tts_engine

    engine = tts_engine.Engine.__new__(tts_engine.Engine)  # no model, so no GPU
    segments = [Speech("ఒకటి", 3.0), Silence(1.0), Speech("రెండు", 5.0)]
    engine.prep = type("Prep", (), {"prepare": staticmethod(lambda text: segments)})
    engine.generate_checked = lambda text, est, voice, seed: (np.zeros(2400, dtype=np.float32), seed, 0.1, False)
    monkeypatch.setattr(tts_engine, "assemble", lambda chunk_dir, wav: (None, 0.2))
    seen = []
    engine.render("x", tmp_path / "a.wav", on_chunk=seen.append)
    assert seen == [3.0, 5.0]


def test_client_stays_ascii_for_powershell_51():
    assert (ROOT / "windows_client" / "tts.ps1").read_bytes().isascii()


def test_client_blocks_a_second_copy_for_commands_that_change_things():
    source = (ROOT / "windows_client" / "tts.ps1").read_text(encoding="ascii")
    assert "System.Threading.Mutex" in source
    assert "@('voice', 'make', 'full', 'watch')" in source


def test_download_all_never_deletes_from_the_server():
    source = (ROOT / "windows_client" / "tts.ps1").read_text(encoding="ascii")
    body = source.split("function Get-AllOutputs", 1)[1].split("# ------", 1)[0]
    assert "'download' { $ok = Get-AllOutputs }" in source
    for server_change in ("job_downloaded", "ssh", "rm "):
        assert server_change not in body


# ---------------------------------------------------------------- [STYLE ...] tags

def test_style_tags_choose_the_voice_and_never_reach_the_speech():
    from textprep import Silence, TextPrep
    from tts_engine import plan_segments

    text = "మొదట. [STYLE excited] చాలా బాగుంది! [PAUSE 1s] ఇంకా బాగుంది! [STYLE funny] హహ నిజమా? [STYLE normal] సరే."
    plan = plan_segments(TextPrep(unknown_log=None), text, "pavan")
    assert [name for _, name in plan] == ["pavan", "pavan_excited", "pavan_excited", "pavan_excited", "pavan_funny", "pavan"]
    assert isinstance(plan[2][0], Silence)
    spoken = " ".join(s.text for s, _ in plan if not isinstance(s, Silence))
    assert "STYLE" not in spoken and "excited" not in spoken and "funny" not in spoken


def test_text_without_tags_is_prepared_exactly_as_before():
    from textprep import TextPrep
    from tts_engine import plan_segments

    prep = TextPrep(unknown_log=None)
    text = "మొదటి వాక్యం. [PAUSE 2s] రెండవ వాక్యం."
    assert [s for s, _ in plan_segments(prep, text, "pavan")] == prep.prepare(text)
    assert {name for _, name in plan_segments(prep, text, "pavan")} == {"pavan"}


def test_each_section_starts_in_the_normal_voice():
    from tts_engine import styled_parts

    assert styled_parts("[STYLE funny] హహ", "pavan")[-1][0] == "pavan_funny"
    assert styled_parts("సరే", "pavan") == [("pavan", "సరే")]


def test_missing_style_recording_stops_the_job_with_instructions(tmp_path, monkeypatch):
    voices = tmp_path / "voices" / "pavan"
    voices.mkdir(parents=True)
    (voices / "ref.wav").write_bytes(b"x")
    (voices / "ref.txt").write_text("x", encoding="utf-8")
    monkeypatch.setattr(job_runner.voicelib, "VOICES", tmp_path / "voices")
    with pytest.raises(job_runner.JobError, match="pavan_funny.*01_voice_clips"):
        job_runner.prepare_voices([("a", "[STYLE funny] హహ")], "pavan", tmp_path / "refs")
    job_runner.prepare_voices([("a", "సరే")], "pavan", tmp_path / "refs")
    assert (tmp_path / "refs" / "pavan.wav").exists()


def test_quality_check_regenerates_with_the_chunks_own_voice():
    source = (ROOT.parent / "telugu-tts" / "qc.py").read_text(encoding="utf-8")
    assert 'seg.get("voice", meta["voice"])' in source
