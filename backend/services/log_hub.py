"""
Log hub — the log bus behind the debug console (ADR-0002).

Two halves:

* the pure hub — ``LogHub``, ``StdoutTee``, ``FileSink``, ``HubLogHandler`` —
  buffers, fans out and rotates entries and touches no global;
* ``install()`` / ``uninstall()`` at the bottom do the process-wide wiring:
  rebind ``sys.stdout`` through the tee and attach the root-logger handler.

ADR-0002 chose the tee over migrating the codebase\'s ``print`` calls to
``logging``, so prints are captured unchanged while framework logs arrive
through the logger handler; both converge here before reaching ``/ws/logs``
subscribers and the rotated ``data/logs/companion.log``.

Entries are ``{source, level, message, ts}``: ``source`` is one of
``backend|renderer|cmd``, ``level`` is normalized to ``debug|info|warn|error``
(incoming "warning"/"critical" fold in, so consumers never see two spellings
of one severity) and ``ts`` is epoch seconds.

Subscribers run synchronously on the thread that logged, so an asyncio
subscriber must marshal to its own loop itself —
``loop.call_soon_threadsafe(queue.put_nowait, entry)``.
"""

from __future__ import annotations

import io
import logging
import sys
import threading
import time
from collections import deque
from logging.handlers import RotatingFileHandler
from pathlib import Path
from typing import Callable

DEFAULT_RING_SIZE = 500

LOG_FILE_NAME = "companion.log"
MAX_LOG_BYTES = 5 * 1024 * 1024
LOG_BACKUP_COUNT = 2

BACKEND = "backend"      # backend prints + framework logs
RENDERER = "renderer"    # frontend error reports
CMD = "cmd"              # debug-console command output

DEBUG = "debug"
INFO = "info"
WARN = "warn"
ERROR = "error"

# Canonical vocabulary: anything else (logging\'s "warning", a future
# "critical") folds into these so consumers never handle two spellings.
_LEVEL_ALIASES = {
    "debug": DEBUG,
    "info": INFO,
    "warn": WARN,
    "warning": WARN,
    "error": ERROR,
    "critical": ERROR,
    "fatal": ERROR,
}


def _level(value: str) -> str:
    """Fold a level name into the hub\'s canonical vocabulary."""
    return _LEVEL_ALIASES.get(str(value).lower(), INFO)


Listener = Callable[[dict], None]


class LogHub:
    """Ring buffer of structured log entries with live subscriber fan-out."""

    def __init__(
        self,
        ring_size: int = DEFAULT_RING_SIZE,
        sink: Callable[[dict], None] | None = None,
    ) -> None:
        self._ring: deque[dict] = deque(maxlen=ring_size)
        self._listeners: list[Listener] = []
        self._lock = threading.Lock()
        self._sink = sink
        self._partial = ""

    def add(self, message: str, source: str = BACKEND, level: str = INFO) -> dict:
        """Append one entry, persist it, and push it to every subscriber."""
        entry = {
            "source": source,
            "level": _level(level),
            "message": message,
            "ts": time.time(),
        }
        with self._lock:
            self._ring.append(entry)
            listeners = list(self._listeners)
            sink = self._sink
        if sink is not None:
            try:
                sink(entry)
            except Exception as exc:
                # A full disk or a locked file must not break the print that
                # produced the line — but it must not go unnoticed either.
                print(f"[log_hub] file sink failed: {exc}", file=sys.stderr, flush=True)
        for listener in listeners:
            try:
                listener(entry)
            except Exception:
                # Per-subscriber failures are expected (closed WS, full queue).
                pass
        return entry

    def feed(self, text: str) -> None:
        """Split raw stream text into lines and add each complete one."""
        if not text:
            return
        with self._lock:
            buffered = self._partial + text
            complete, newline, self._partial = buffered.rpartition("\n")
            if not newline:
                self._partial = buffered
                return
        for line in complete.split("\n"):
            self.add(line.rstrip("\r"))

    def flush(self) -> None:
        """Emit a line still waiting for its newline (``print(..., end="")``)."""
        with self._lock:
            pending, self._partial = self._partial, ""
        if pending:
            self.add(pending.rstrip("\r"))

    def lines(self) -> list[dict]:
        """Snapshot of the ring buffer, oldest first."""
        with self._lock:
            return list(self._ring)

    @property
    def ring_size(self) -> int:
        """Capacity of the ring buffer."""
        return self._ring.maxlen or 0

    @property
    def subscriber_count(self) -> int:
        """How many listeners are currently subscribed."""
        with self._lock:
            return len(self._listeners)

    @property
    def log_file(self) -> Path | None:
        """Where the established sink writes, or None when nothing persists.

        Only a :class:`FileSink` reports a path; any callable works as a sink.
        """
        return getattr(self._sink, "path", None)

    def clear(self) -> None:
        """Drop every buffered entry (the debug console\'s ``clear`` command)."""
        with self._lock:
            self._ring.clear()

    def subscribe(self, listener: Listener) -> Callable[[], None]:
        """Register *listener*; returns the callable that unregisters it."""
        with self._lock:
            self._listeners.append(listener)

        def unsubscribe() -> None:
            with self._lock:
                if listener in self._listeners:
                    self._listeners.remove(listener)

        return unsubscribe


class StdoutTee(io.TextIOBase):
    """Writes through to the real stdout and feeds each complete line to a hub."""

    def __init__(self, hub: LogHub, stream) -> None:
        self._hub = hub
        self._stream = stream

    def write(self, text: str) -> int:
        written = self._stream.write(text)
        self._hub.feed(text)
        return len(text) if written is None else written

    def flush(self) -> None:
        self._stream.flush()
        self._hub.flush()

    def __getattr__(self, name: str):
        # isatty/fileno/encoding/buffer/... belong to the stream we wrap.
        if name.startswith("_"):
            raise AttributeError(name)
        return getattr(self._stream, name)


class FileSink:
    """Appends hub entries to a size-rotated log file (5 MB x 2 by default)."""

    def __init__(
        self,
        path: str | Path,
        max_bytes: int = MAX_LOG_BYTES,
        backup_count: int = LOG_BACKUP_COUNT,
    ) -> None:
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        self.path = path
        self._handler = RotatingFileHandler(
            path,
            maxBytes=max_bytes,
            backupCount=backup_count,
            encoding="utf-8",
        )
        self._handler.setFormatter(
            logging.Formatter("%(asctime)s %(levelname)s %(message)s")
        )

    def __call__(self, entry: dict) -> None:
        # Rotation is the stdlib handler\'s job (ADR-0002); it only takes a
        # LogRecord, so build the minimal one it needs.
        record = logging.LogRecord(
            name=entry["source"],
            level=getattr(logging, str(entry["level"]).upper(), logging.INFO),
            pathname="",
            lineno=0,
            msg=entry["message"],
            args=None,
            exc_info=None,
        )
        # Keep the file timestamp identical to the entry the console shows.
        record.created = entry["ts"]
        record.msecs = (entry["ts"] % 1) * 1000
        self._handler.emit(record)


class HubLogHandler(logging.Handler):
    """Root-logger bridge so framework logs (uvicorn, sqlalchemy) reach the hub."""

    def __init__(self, hub: LogHub) -> None:
        super().__init__(level=logging.INFO)
        self._hub = hub

    def emit(self, record: logging.LogRecord) -> None:
        try:
            message = record.getMessage()
        except Exception:
            return
        self._hub.add(message, source=BACKEND, level=record.levelname)


class _Wiring:
    """What install() changed, so uninstall() can put it back."""

    def __init__(self, hub: LogHub, stdout, logger: logging.Logger, handler: logging.Handler) -> None:
        self.hub = hub
        self.stdout = stdout
        self.logger = logger
        self.handler = handler


_wiring: _Wiring | None = None


def install(
    hub: LogHub | None = None,
    *,
    stream=None,
    log_dir=None,
    root: logging.Logger | None = None,
) -> LogHub:
    """Wire a hub into stdout and the root logger.  Idempotent.

    *stream* defaults to the current stdout and stays the tee\'s write-through
    target, *log_dir* to ``<data_dir>/logs``, *root* to the root logger.
    Calling install() again returns the hub from the first call untouched;
    :func:`uninstall` undoes the wiring.
    """
    global _wiring
    if _wiring is not None:
        return _wiring.hub

    if log_dir is None:
        from config import settings

        log_dir = settings.resolve_data_dir() / "logs"

    logger = root if root is not None else logging.getLogger()
    hub = hub or LogHub(sink=FileSink(Path(log_dir) / LOG_FILE_NAME))
    previous_stdout = sys.stdout
    handler = HubLogHandler(hub)
    sys.stdout = StdoutTee(hub, previous_stdout if stream is None else stream)
    logger.addHandler(handler)
    _wiring = _Wiring(hub, previous_stdout, logger, handler)
    return hub


def uninstall() -> None:
    """Undo :func:`install`: restore stdout and detach the logger handler."""
    global _wiring
    if _wiring is None:
        return
    sys.stdout = _wiring.stdout
    _wiring.logger.removeHandler(_wiring.handler)
    _wiring = None
