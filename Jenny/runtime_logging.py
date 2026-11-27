"""Mirror Python stdout and stderr to a per-session runtime log file."""
from __future__ import annotations

import logging
import re
import sys
import threading
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import TextIO
from uuid import uuid4


_CONSOLE_LEVEL = re.compile(
    r"^(?:\[[^\]]+\]\s+\[)?(DEBUG|INFO|WARNING|ERROR|CRITICAL)(?:\s*\]|:|\s+-)"
)


class _TeeStream:
    """Forward terminal output unchanged while writing complete lines to a logger."""

    def __init__(self, console: TextIO, logger: logging.Logger, level: int) -> None:
        self.console = console
        self.logger = logger
        self.level = level
        self.pending = ""
        self.lock = threading.RLock()
        self.encoding = getattr(console, "encoding", "utf-8")

    def write(self, value: str) -> int:
        text = str(value)
        with self.lock:
            written = self.console.write(text)
            self.pending += text
            while "\n" in self.pending:
                line, self.pending = self.pending.split("\n", 1)
                self._log_line(line)
            return len(text) if written is None else written

    def flush(self) -> None:
        with self.lock:
            self.console.flush()
            if self.pending:
                self._log_line(self.pending)
                self.pending = ""

    def _log_line(self, line: str) -> None:
        match = _CONSOLE_LEVEL.match(line)
        level = logging.getLevelNamesMapping()[match[1]] if match else self.level
        self.logger.log(level, line.rstrip("\r"))

    def isatty(self) -> bool:
        return bool(getattr(self.console, "isatty", lambda: False)())

    def fileno(self) -> int:
        return self.console.fileno()

    def __getattr__(self, name: str):
        return getattr(self.console, name)


def configure_runtime_logging() -> None:
    """Start a fresh dated log session while retaining earlier sessions."""
    if getattr(sys, "_jenny_runtime_logging_enabled", False):
        return
    project_root = Path(__file__).resolve().parents[1]
    log_dir = project_root / "logs"
    log_dir.mkdir(parents=True, exist_ok=True)
    logger = logging.getLogger("jenny.runtime")
    logger.setLevel(logging.INFO)
    logger.propagate = False
    latest = log_dir / "python-latest.log"
    if latest.exists():
        with latest.open(encoding="utf-8", errors="replace") as stream:
            first_line = stream.readline()
        try:
            started = datetime.fromisoformat(first_line.split(" ", 1)[0])
        except ValueError:
            started = datetime.fromtimestamp(latest.stat().st_mtime, timezone.utc)
        archived = log_dir / f"python-{started:%Y-%m-%d_%H-%M-%S}Z.log"
        if archived.exists():
            archived = log_dir / f"python-{started:%Y-%m-%d_%H-%M-%S}Z-{uuid4().hex[:8]}.log"
        latest.rename(archived)
    handler = logging.FileHandler(latest, encoding="utf-8")
    formatter = logging.Formatter("%(asctime)sZ %(levelname)s %(message)s", datefmt="%Y-%m-%dT%H:%M:%S")
    formatter.converter = time.gmtime
    handler.setFormatter(formatter)
    logger.addHandler(handler)
    sys.stdout = _TeeStream(sys.__stdout__, logger, logging.INFO)  # type: ignore[assignment]
    sys.stderr = _TeeStream(sys.__stderr__, logger, logging.ERROR)  # type: ignore[assignment]
    sys._jenny_runtime_logging_enabled = True
