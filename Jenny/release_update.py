"""Read published releases and install a verified fast-forward in a supervised clone."""

from pathlib import Path
import os
import re
import shutil
import subprocess
from urllib.parse import quote

import requests


ROOT = Path(__file__).resolve().parent.parent
REPOSITORY = "MotrynStudios/Jenny-AI-Discord-Bot"
ORIGIN = f"https://github.com/{REPOSITORY}.git"
API = f"https://api.github.com/repos/{REPOSITORY}"
DEPENDENCIES = ("requirements.txt", "pyproject.toml", "voice_service/package.json", "voice_service/package-lock.json")
ENTRY_POINTS = ("Jenny/Jenny-Bot.py", "voice_service/index.js", "tools/run_supervised.py")


class ReleaseError(Exception):
    """A release operation cannot proceed and can be explained to the operator."""


def github_json(path):
    response = requests.get(f"{API}/{path}", headers={"Accept": "application/vnd.github+json"}, timeout=(4, 8))
    response.raise_for_status()
    value = response.json()
    if not isinstance(value, dict):
        raise ValueError("Expected a GitHub object")
    return value


def valid_tag(value):
    return isinstance(value, str) and bool(re.fullmatch(r"v?[0-9][0-9A-Za-z._-]{0,62}", value)) and ".." not in value


def valid_commit(value):
    return isinstance(value, str) and bool(re.fullmatch(r"[0-9a-f]{40}", value))


def latest_release():
    release = github_json("releases/latest")
    if not valid_tag(release.get("tag_name")):
        raise ValueError("GitHub did not provide a valid release tag")
    return release


def release_commit(tag):
    if not valid_tag(tag):
        raise ReleaseError("Invalid release tag.")
    commit = github_json(f"commits/{quote(tag, safe='')}").get("sha")
    if not valid_commit(commit):
        raise ReleaseError("GitHub did not provide a valid release commit.")
    return commit


def git(root, *args, raw=False, timeout=12):
    """Run a fixed Git invocation locally. Keep provider output out of Discord."""
    try:
        command = subprocess.run(
            ["git", "-C", str(root), *args], capture_output=True, timeout=timeout,
            env={**os.environ, "GIT_TERMINAL_PROMPT": "0"},
        )
    except (OSError, subprocess.TimeoutExpired) as error:
        raise ReleaseError("Git is unavailable or did not respond in time.") from error
    if command.returncode:
        raise ReleaseError(f"Git could not complete {args[0]}.")
    return command.stdout if raw else command.stdout.decode("utf-8", errors="replace").strip()


def local_version(root=ROOT):
    if (root / ".git").exists():
        try:
            return "Checkout tag", git(root, "describe", "--tags", "--exact-match", "HEAD")
        except ReleaseError:
            pass
    try:
        changelog = (root / "CHANGELOG.md").read_text(encoding="utf-8")
    except (OSError, UnicodeError):
        return "Local changelog version", "Unknown"
    match = re.search(r"(?m)^##\s+v?([0-9][^\s]*)", changelog)
    return "Local changelog version", match[1] if match else "Unknown"


def check_checkout(root=ROOT):
    if not (root / ".git").exists():
        raise ReleaseError("Installing updates requires a Git clone. This folder has no .git metadata.")
    origin = git(root, "remote", "get-url", "origin").rstrip("/").removesuffix(".git")
    if origin.casefold() != ORIGIN.removesuffix(".git").casefold():
        raise ReleaseError("Use a clone with the official Jenny HTTPS repository as its origin.")
    if git(root, "branch", "--show-current") != "main" or git(root, "status", "--porcelain"):
        raise ReleaseError("Switch to main and save local source changes before installing an update.")


def validate_candidate(root, current, target, *, voice):
    if git(root, "diff", "--name-only", current, target, "--", *DEPENDENCIES):
        raise ReleaseError("This release changes dependencies. Install it from the host and refresh those dependencies.")
    paths = git(root, "diff", "--name-only", "-z", "--diff-filter=ACMRT", current, target, raw=True).split(b"\0")
    for entry in ENTRY_POINTS:
        try:
            git(root, "cat-file", "-e", f"{target}:{entry}")
        except ReleaseError as error:
            raise ReleaseError(f"The release is missing {entry}.") from error
    for path in filter(None, paths):
        name = path.decode("utf-8", errors="strict")
        filename = Path(name).name.lower()
        if (name.lower().startswith(("logs/", "data/", "mods/", "config/")) and filename != ".gitkeep"
                or filename.startswith(".env") and filename != ".env.example"
                or filename.startswith("memory.db")):
            raise ReleaseError("The release includes files reserved for local settings or runtime data.")
        if name.endswith(".py"):
            source = git(root, "show", f"{target}:{name}", raw=True)
            try:
                compile(source, name, "exec")
            except (SyntaxError, ValueError) as error:
                raise ReleaseError(f"The release has a Python syntax error in {name}.") from error
        elif voice and name.endswith(".js"):
            if not shutil.which("node"):
                raise ReleaseError("Install Node.js to check a release that changes the voice service.")
            source = git(root, "show", f"{target}:{name}", raw=True)
            try:
                check = subprocess.run(["node", "--check"], input=source, capture_output=True, timeout=8)
            except (OSError, subprocess.TimeoutExpired) as error:
                raise ReleaseError("Node.js did not complete the release check.") from error
            if check.returncode:
                raise ReleaseError(f"The release has a JavaScript syntax error in {name}.")


def apply_release(tag, target, *, voice=False, root=ROOT):
    """Fetch the official pinned release and preserve every tracked or ignored local change."""
    if not valid_tag(tag) or not valid_commit(target):
        raise ReleaseError("The release request was invalid.")
    check_checkout(root)
    current = git(root, "rev-parse", "HEAD")
    if current == target:
        return False
    git(root, "fetch", "--no-tags", ORIGIN, f"refs/tags/{tag}", timeout=45)
    fetched = git(root, "rev-parse", "--verify", "FETCH_HEAD^{commit}")
    if fetched != target:
        raise ReleaseError("The release tag moved after Jenny checked it. Check for updates again.")
    try:
        git(root, "merge-base", "--is-ancestor", current, target)
    except ReleaseError as error:
        raise ReleaseError("The published release cannot fast-forward this checkout.") from error
    validate_candidate(root, current, target, voice=voice)
    check_checkout(root)
    if git(root, "rev-parse", "HEAD") != current:
        raise ReleaseError("The checkout changed while Jenny checked the release.")
    try:
        git(root, "merge", "--ff-only", "--no-overwrite-ignore", target, timeout=30)
    except ReleaseError as error:
        raise ReleaseError("Git refused to fast-forward without overwriting local files.") from error
    return True
