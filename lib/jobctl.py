#!/usr/bin/env python3
"""Job and voice bookkeeping. Standard library only, so it starts fast and needs no GPU.

  jobctl.py create <name> <voice> <mode> <test_only:true|false> [seed]
  jobctl.py status <job_dir> | list [limit] | files <job_dir> | log <job_dir> [lines]
  jobctl.py mark_downloaded <job_dir> | clone_full <job_dir> | last_test
  jobctl.py voices | check_start <job_dir> | set <job_dir> key=value ...

Everything printed on stdout is ASCII JSON (Telugu arrives as \\uXXXX), which survives
any Windows console code page. Failures print one plain sentence on stderr and exit 1.
"""
import fcntl
import json
import os
import re
import shutil
import sys
import tempfile
import time
import unicodedata
from pathlib import Path

HOME = Path(os.environ.get("VOICE_HOME", "/home/pavan/voice"))
JOBS = HOME / "jobs"
VOICES = Path(os.environ.get("VOICES_DIR", HOME / "voices"))
JOB_NAME = re.compile(r"^\d{8}-\d{6}_[a-z0-9-]{1,40}$")
MODES = ("script", "slides")
DEFAULT_SEED = 42


class Fail(Exception):
    """A problem to show the user as one plain sentence."""


def now() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%S%z")


def dump(obj) -> None:
    print(json.dumps(obj, ensure_ascii=True))


def write_json(path: Path, obj) -> None:
    """Atomic, so a reader polling over SSH never sees half a file."""
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=".tmp_", suffix=".json")
    with os.fdopen(fd, "w", encoding="utf-8") as handle:
        json.dump(obj, handle, ensure_ascii=True, indent=1)
        handle.write("\n")
    os.chmod(tmp, 0o664)
    os.replace(tmp, path)


def read_json(path: Path):
    return json.loads(path.read_text(encoding="utf-8"))


def valid_voice_name(name: str) -> bool:
    """Letters, digits and marks of any script (Telugu vowel signs are marks), plus - and _."""
    if not name or len(name) > 64 or name[0] in "._-":
        return False
    return all(c in "-_" or unicodedata.category(c)[0] in "LMN" for c in name)


def voice_names() -> list:
    if not VOICES.is_dir():
        return []
    return sorted(p.name for p in VOICES.iterdir()
                  if p.is_dir() and valid_voice_name(p.name) and (p / "ref.wav").is_file() and (p / "ref.txt").is_file())


def require_voice(voice: str) -> None:
    available = voice_names()
    if voice not in available:
        listed = ", ".join(available) if available else "none yet"
        raise Fail(f"Voice '{voice}' not found. Available voices: {listed}. Add it with 1_Add_My_Voice first.")


def job_path(text: str) -> Path:
    """Accepts a job name or path. Only real folders directly inside jobs/ are allowed."""
    path = Path(text)
    name = path.name
    if not JOB_NAME.match(name) or (path.is_absolute() and path.parent != JOBS):
        raise Fail(f"Not a valid job folder: {text}")
    job = JOBS / name
    if job.is_symlink() or not job.is_dir():
        raise Fail(f"No such job: {name}")
    return job


def slugify(name: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")[:40].strip("-")
    return slug or "script"


def new_job_dir(name: str) -> Path:
    JOBS.mkdir(parents=True, exist_ok=True)
    slug = slugify(name)
    for _ in range(30):  # one second apart at most; mkdir decides who wins
        job = JOBS / f"{time.strftime('%Y%m%d-%H%M%S')}_{slug}"
        try:
            job.mkdir()
            return job
        except FileExistsError:
            time.sleep(1)
    raise Fail("Could not choose a new job folder name. Try again in a minute.")


def write_new_job(job: Path, spec: dict) -> None:
    for sub in ("in", "out", "logs", "work"):
        (job / sub).mkdir(exist_ok=True)
    write_json(job / "job.json", spec)
    write_json(job / "status.json", {"state": "queued", "progress": "0/0", "phase": "waiting for upload",
                                     "started": None, "finished": None, "error": None})
    (job / "job.log").touch()


def cmd_create(name, voice, mode, test_only, seed=str(DEFAULT_SEED)):
    require_voice(voice)
    if mode not in MODES:
        raise Fail(f"Mode must be one of {MODES}, not '{mode}'.")
    if test_only not in ("true", "false"):
        raise Fail("test_only must be true or false.")
    if not re.fullmatch(r"\d{1,9}", seed):
        raise Fail("Seed must be a whole number.")
    job = new_job_dir(name)
    write_new_job(job, {"name": name, "voice": voice, "mode": mode, "test_only": test_only == "true",
                        "seed": int(seed), "created": now()})
    dump({"job_dir": str(job), "job": job.name})


def alive(job: Path) -> bool:
    """A running job holds .job.lock for its whole life, so this also survives PID reuse."""
    lock = job / ".job.lock"
    if not lock.exists():
        return False
    with lock.open("a") as handle:
        try:
            fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            return True
        fcntl.flock(handle, fcntl.LOCK_UN)
    return False


def load_status(job: Path) -> dict:
    """Reads status.json. A job whose process died (reboot, kill) is reported as failed."""
    try:
        status = read_json(job / "status.json")
    except (OSError, ValueError) as error:
        raise Fail(f"Could not read the status of {job.name}: {error}")
    if status.get("state") in ("queued", "running") and status.get("launched") and not alive(job):
        status.update(state="failed", finished=now(),
                      error="The job stopped unexpectedly (server reboot or crash). Run it again to continue from where it stopped.")
        write_json(job / "status.json", status)
    return status


def cmd_status(job_text):
    job = job_path(job_text)
    status = load_status(job)
    status["job"] = job.name
    status["downloaded"] = (job / ".downloaded").exists()
    dump(status)


def is_job(path: Path) -> bool:
    return JOB_NAME.match(path.name) and path.is_dir() and not path.is_symlink() and (path / "job.json").is_file()


def all_jobs() -> list:
    return sorted((p for p in JOBS.iterdir() if is_job(p)), key=lambda p: p.name, reverse=True) if JOBS.is_dir() else []


def summary(job: Path) -> dict:
    spec, status = read_json(job / "job.json"), load_status(job)
    return {"job": job.name, "job_dir": str(job), "name": spec.get("name"), "voice": spec.get("voice"),
            "mode": spec.get("mode"), "test_only": spec.get("test_only"), "state": status.get("state"),
            "launched": bool(status.get("launched")), "phase": status.get("phase"), "progress": status.get("progress"),
            "started": status.get("started"), "finished": status.get("finished"), "error": status.get("error"),
            "downloaded": (job / ".downloaded").exists(), "full_job": spec.get("full_job")}


def cmd_list(limit="15"):
    dump([summary(j) for j in all_jobs()[: int(limit)]])


def cmd_files(job_text):
    """Exact names and sizes, so Windows can prove a download is complete."""
    out = job_path(job_text) / "out"
    dump([{"name": p.name, "size": p.stat().st_size} for p in sorted(out.iterdir()) if p.is_file()])


def cmd_log(job_text, lines="20"):
    log = job_path(job_text) / "job.log"
    text = log.read_text(encoding="utf-8", errors="replace") if log.exists() else ""
    dump({"lines": text.splitlines()[-int(lines):]})


def cmd_mark_downloaded(job_text):
    job = job_path(job_text)
    if load_status(job).get("state") != "done":
        raise Fail("This job is not finished, so it cannot be marked as downloaded.")
    (job / ".downloaded").write_text(now() + "\n", encoding="utf-8")
    dump({"job": job.name, "downloaded": True})


def cmd_last_test():
    """The newest finished test job, or null."""
    for job in all_jobs():
        spec = read_json(job / "job.json")
        if spec.get("test_only") and load_status(job).get("state") == "done":
            dump(summary(job))
            return
    dump(None)


def cmd_clone_full(job_text):
    """A full run of a test job. Running this twice returns the same full job."""
    source = job_path(job_text)
    spec = read_json(source / "job.json")
    if not spec.get("test_only"):
        raise Fail(f"{source.name} is already a full job.")
    if spec.get("full_job"):
        try:
            existing = job_path(spec["full_job"])
            dump({"job_dir": str(existing), "job": existing.name, "existing": True})
            return
        except Fail:
            pass  # the earlier full job is gone, so make a new one
    require_voice(spec["voice"])
    job = new_job_dir(spec["name"])
    write_new_job(job, {**{k: spec[k] for k in ("name", "voice", "mode", "seed")}, "test_only": False,
                        "approved_from": source.name, "created": now()})
    for item in (source / "in").iterdir():
        if item.is_file():
            shutil.copy2(item, job / "in" / item.name)
    spec["full_job"] = job.name
    write_json(source / "job.json", spec)
    dump({"job_dir": str(job), "job": job.name, "existing": False})


def cmd_voices():
    rows = []
    for name in voice_names():
        try:
            meta = read_json(VOICES / name / "meta.json")
        except (OSError, ValueError):
            meta = {"meta_error": "meta.json is missing or unreadable"}
        rows.append({"name": name, **meta})
    dump(rows)


def inputs_problem(job: Path, spec: dict):
    files = [p for p in (job / "in").iterdir() if p.is_file() and p.name != "job.json"]
    if spec.get("mode") == "script" and not any(p.suffix.lower() in (".md", ".txt") for p in files):
        return "No script was uploaded (expected in/script.md)."
    if spec.get("mode") == "slides" and not any(p.suffix.lower() == ".txt" for p in files):
        return "No slide text files were uploaded (expected in/slide_01.txt and so on)."
    return None


def cmd_check_start(job_text):
    """Checks everything that can fail before going to the background."""
    job = job_path(job_text)
    try:
        spec = read_json(job / "job.json")
    except (OSError, ValueError) as error:
        raise Fail(f"job.json is missing or broken: {error}")
    if spec.get("mode") not in MODES:
        raise Fail(f"job.json has an unknown mode: {spec.get('mode')}")
    require_voice(spec.get("voice", ""))
    problem = inputs_problem(job, spec)
    if problem:
        raise Fail(problem)
    dump({"ok": True})


def cmd_set(job_text, *pairs):
    job = job_path(job_text)
    status = read_json(job / "status.json")
    for pair in pairs:
        key, _, value = pair.partition("=")
        status[key] = {"": None, "true": True, "false": False}.get(value, value)
    write_json(job / "status.json", status)


COMMANDS = {"create": cmd_create, "status": cmd_status, "list": cmd_list, "files": cmd_files, "log": cmd_log,
            "mark_downloaded": cmd_mark_downloaded, "last_test": cmd_last_test, "clone_full": cmd_clone_full,
            "voices": cmd_voices, "check_start": cmd_check_start, "set": cmd_set}


def main() -> int:
    if len(sys.argv) < 2 or sys.argv[1] not in COMMANDS:
        print(f"usage: jobctl.py {{{'|'.join(COMMANDS)}}} ...", file=sys.stderr)
        return 2
    try:
        COMMANDS[sys.argv[1]](*sys.argv[2:])
    except Fail as error:
        print(str(error), file=sys.stderr)
        return 1
    except TypeError:
        print(f"Wrong arguments for '{sys.argv[1]}'.", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())
