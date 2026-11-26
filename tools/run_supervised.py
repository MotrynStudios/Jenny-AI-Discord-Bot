"""Supervise Jenny and apply approved official releases."""

import argparse
import json
import os
from pathlib import Path
import subprocess
import sys
from uuid import uuid4

from dotenv import load_dotenv


ROOT = Path(__file__).resolve().parents[1]
RUNTIME = ROOT / "logs"
REQUEST = RUNTIME / "supervisor-request.json"
RESULT = RUNTIME / "supervisor-result.json"

sys.path.insert(0, str(ROOT / "Jenny"))
from release_update import REPOSITORY, ReleaseError, apply_release


def operation_result(payload, *, voice):
    if payload["action"] == "restart":
        return {"title": "Jenny restarted", "detail": "I’m back online.", "error": False}
    try:
        tag = payload["tag"]
        changed = apply_release(tag, payload["target"], voice=voice)
        return {
            "title": f"Updated to {tag}" if changed else f"Already on {tag}",
            "detail": str(payload.get("notes") or "See GitHub for release notes.")[:1300]
                      + f"\n\n[Full release notes](https://github.com/{REPOSITORY}/releases/tag/{tag})",
            "error": False,
        }
    except (KeyError, OSError, UnicodeError, ReleaseError) as error:
        print(f"[SUPERVISOR] Could not install release: {error}", flush=True)
        detail = str(error) if isinstance(error, ReleaseError) else "Git could not read some of the release files."
        return {"title": "Update unavailable", "detail": detail, "error": True}


def launch_voice():
    return subprocess.Popen(["node", "index.js"], cwd=ROOT / "voice_service")


def stop_voice(process):
    if process and process.poll() is None:
        process.terminate()
        try:
            process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--voice", action="store_true", help="Also supervise the optional Node voice service.")
    args = parser.parse_args()
    load_dotenv(ROOT / ".env")
    RUNTIME.mkdir(exist_ok=True)
    env = {**os.environ, "JENNY_SUPERVISOR_SESSION": uuid4().hex}
    while True:
        voice = launch_voice() if args.voice else None
        try:
            status = subprocess.call([sys.executable, str(ROOT / "Jenny" / "Jenny-Bot.py")], cwd=ROOT, env=env)
        finally:
            stop_voice(voice)
        if not REQUEST.is_file():
            return status
        try:
            payload = json.loads(REQUEST.read_text(encoding="utf-8"))
            REQUEST.unlink()
            if (payload["session"] != env["JENNY_SUPERVISOR_SESSION"]
                    or payload["action"] not in {"restart", "update"}):
                return status
            if not all(isinstance(payload.get(name), int) and payload[name] > 0 for name in ("guild", "channel")):
                return status
        except (OSError, KeyError, ValueError):
            return status
        outcome = {**payload, **operation_result(payload, voice=args.voice)}
        temp = RESULT.with_suffix(".new")
        temp.write_text(json.dumps(outcome), encoding="utf-8")
        temp.replace(RESULT)


if __name__ == "__main__":
    raise SystemExit(main())
