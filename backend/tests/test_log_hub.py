"""
Tests for LogHub — the log bus behind the debug console (ADR-0002).

Seam: services.log_hub.  The hub, tee and file sink are driven directly with
their own streams and directories; the install() tests go through the public
install()/uninstall() pair, so no test reaches into module globals or leaves
the process-wide stdout hijacked.
"""

import io
import logging
import sys
import uuid

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

import services.log_hub as log_hub
from api.logs import router as logs_router
from services.log_hub import FileSink, LogHub, StdoutTee


class TestStdoutTee:
    def test_print_lands_in_hub_and_still_reaches_the_console(self):
        console = io.StringIO()
        hub = LogHub()
        tee = StdoutTee(hub, console)

        print("hello world", file=tee)

        assert console.getvalue() == "hello world\n"   # write-through kept
        entries = hub.lines()
        assert len(entries) == 1
        entry = entries[0]
        assert entry["message"] == "hello world"
        assert entry["source"] == "backend"
        assert entry["level"] == "info"
        assert isinstance(entry["ts"], float) and entry["ts"] > 0

    def test_print_is_a_single_entry_not_two_writes(self):
        """print() writes the text and the trailing newline separately."""
        hub = LogHub()
        tee = StdoutTee(hub, io.StringIO())

        print("split me", file=tee)

        assert [e["message"] for e in hub.lines()] == ["split me"]

    def test_unterminated_write_waits_for_its_newline(self):
        hub = LogHub()
        tee = StdoutTee(hub, io.StringIO())

        tee.write("half a li")
        assert hub.lines() == []

        tee.write("ne\n")
        assert [e["message"] for e in hub.lines()] == ["half a line"]

    def test_flush_emits_a_line_that_never_got_its_newline(self):
        """print(..., end="", flush=True) is this codebase\'s progress idiom."""
        hub = LogHub()
        tee = StdoutTee(hub, io.StringIO())

        print("progress 40%", end="", file=tee, flush=True)

        assert [e["message"] for e in hub.lines()] == ["progress 40%"]


class TestRingBuffer:
    def test_ring_keeps_the_newest_and_drops_the_oldest(self):
        hub = LogHub(ring_size=500)

        for i in range(600):
            hub.add(f"line {i}")

        messages = [e["message"] for e in hub.lines()]
        assert len(messages) == 500
        assert messages[0] == "line 100"    # the first 100 fell off
        assert messages[-1] == "line 599"

    def test_ring_size_is_configurable(self):
        hub = LogHub(ring_size=2)

        for i in range(5):
            hub.add(f"line {i}")

        assert [e["message"] for e in hub.lines()] == ["line 3", "line 4"]


class TestSubscribers:
    def test_subscriber_receives_each_new_entry(self):
        hub = LogHub()
        received = []
        hub.subscribe(received.append)

        hub.add("first")
        hub.add("second")

        assert [e["message"] for e in received] == ["first", "second"]

    def test_unsubscribe_stops_delivery(self):
        hub = LogHub()
        received = []
        unsubscribe = hub.subscribe(received.append)

        hub.add("before")
        unsubscribe()
        hub.add("after")

        assert [e["message"] for e in received] == ["before"]

    def test_a_broken_subscriber_does_not_break_the_writer(self):
        """A closed WS or full queue must not kill the print that logged."""
        hub = LogHub()
        healthy = []
        hub.subscribe(lambda entry: (_ for _ in ()).throw(RuntimeError("ws gone")))
        hub.subscribe(healthy.append)

        hub.add("still logged")

        assert hub.lines()[-1]["message"] == "still logged"
        assert [e["message"] for e in healthy] == ["still logged"]


class TestLevelVocabulary:
    def test_logging_level_names_fold_into_one_vocabulary(self):
        """Consumers must never see both "warning" and "warn" for one severity."""
        hub = LogHub()

        hub.add("a", level="warning")
        hub.add("b", level="warn")
        hub.add("c", level="critical")

        assert [e["level"] for e in hub.lines()] == ["warn", "warn", "error"]

    def test_unknown_level_falls_back_to_info(self):
        hub = LogHub()
        hub.add("x", level="chatty")
        assert hub.lines()[0]["level"] == "info"


@pytest.fixture
def install_hub(tmp_path):
    """Install a hub from inside the test body; uninstall on teardown.

    The install has to happen in the test *call* phase: pytest swaps
    ``sys.stdout`` for its capture object each phase, so a Tee installed
    from a fixture (setup phase) would be clobbered before the test could print.
    """
    def _install(label: str = "test.log_hub"):
        console = io.StringIO()
        logger = logging.getLogger(f"{label}.{uuid.uuid4().hex}")
        hub = log_hub.install(stream=console, log_dir=tmp_path, root=logger)
        return hub, console, logger

    yield _install
    log_hub.uninstall()


class TestInstall:
    def test_install_tees_stdout_and_writes_the_log_file(self, install_hub, tmp_path):
        hub, console, _ = install_hub()

        print("legacy print survives")

        # The console still gets it (ADR-0002: a tee, not a redirect)...
        assert console.getvalue() == "legacy print survives\n"
        # ...the hub has it...
        assert [e["message"] for e in hub.lines()] == ["legacy print survives"]
        # ...and so does the rotated log file.
        log_file = tmp_path / "companion.log"
        assert "legacy print survives" in log_file.read_text(encoding="utf-8")

    def test_install_is_idempotent(self, install_hub, tmp_path):
        hub, console, logger = install_hub()

        again = log_hub.install(stream=io.StringIO(), log_dir=tmp_path, root=logger)
        print("logged once")

        assert again is hub
        assert [e["message"] for e in hub.lines()] == ["logged once"]
        assert console.getvalue() == "logged once\n"

    def test_framework_logs_reach_the_hub(self, install_hub):
        hub, _, logger = install_hub()

        logger.warning("uvicorn-style record")

        entry = hub.lines()[-1]
        assert entry["message"] == "uvicorn-style record"
        assert entry["source"] == "backend"
        assert entry["level"] == "warn"

    def test_uninstall_gives_stdout_back(self, tmp_path):
        real_stdout = sys.stdout

        log_hub.install(
            stream=io.StringIO(),
            log_dir=tmp_path,
            root=logging.getLogger(f"test.log_hub.{uuid.uuid4().hex}"),
        )
        log_hub.uninstall()

        assert sys.stdout is real_stdout


class TestFileSink:
    def test_log_file_rotates_at_the_size_limit(self, tmp_path):
        sink = FileSink(tmp_path / "companion.log", max_bytes=200, backup_count=1)

        for i in range(50):
            sink({
                "source": "backend",
                "level": "info",
                "message": f"line {i:03d} padded out",
                "ts": 0.0,
            })

        assert (tmp_path / "companion.log").exists()
        assert (tmp_path / "companion.log.1").exists()

def _logs_app(hub: LogHub) -> FastAPI:
    """Just the logs route — no lifespan, so no process-wide Tee is installed."""
    app = FastAPI()
    app.state.log_hub = hub
    app.include_router(logs_router)
    return app


class TestLogsChannel:
    def test_connecting_receives_the_history_then_live_lines(self):
        hub = LogHub()
        hub.add("history one")
        hub.add("history two")

        with TestClient(_logs_app(hub)) as client:
            with client.websocket_connect("/ws/logs") as ws:
                history = ws.receive_json()
                assert history["type"] == "logs_history"
                assert [l["message"] for l in history["lines"]] == [
                    "history one",
                    "history two",
                ]

                hub.add("live line")        # as if a print happened elsewhere
                pushed = ws.receive_json()
                assert pushed["type"] == "logs_line"
                assert pushed["line"]["message"] == "live line"

    def test_renderer_report_comes_back_as_a_live_line(self):
        hub = LogHub()

        with TestClient(_logs_app(hub)) as client:
            with client.websocket_connect("/ws/logs") as ws:
                ws.receive_json()           # history
                ws.send_json(
                    {"type": "renderer_log", "level": "error", "message": "boom"}
                )

                # It entered the hub, so it comes straight back down the wire.
                pushed = ws.receive_json()
                assert pushed["line"]["source"] == "renderer"
                assert pushed["line"]["level"] == "error"
                assert pushed["line"]["message"] == "boom"

    def test_each_client_gets_its_own_history_snapshot(self):
        hub = LogHub()
        hub.add("first")

        with TestClient(_logs_app(hub)) as client:
            with client.websocket_connect("/ws/logs") as first:
                assert [l["message"] for l in first.receive_json()["lines"]] == ["first"]

                hub.add("second")
                with client.websocket_connect("/ws/logs") as second:
                    snapshot = second.receive_json()["lines"]
                    assert [l["message"] for l in snapshot] == ["first", "second"]
                    # the client that was already connected sees it live
                    assert first.receive_json()["line"]["message"] == "second"
    def test_command_output_comes_back_over_the_channel(self):
        from services.command_executor import CommandExecutor

        hub = LogHub()
        app = _logs_app(hub)
        app.state.command_executor = CommandExecutor(hub)

        with TestClient(app) as client:
            with client.websocket_connect("/ws/logs") as ws:
                ws.receive_json()                       # history
                ws.send_json({"type": "command", "command": "help"})

                pushed = ws.receive_json()
                assert pushed["line"]["source"] == "cmd"
                assert pushed["line"]["message"].startswith("available: ")


class TestLogHubMaintenance:
    """The accessors the panel\'s clear/status commands need."""

    def test_clear_drops_every_buffered_entry(self):
        hub = LogHub()
        hub.add("one")
        hub.add("two")

        hub.clear()

        assert hub.lines() == []

    def test_ring_size_and_subscriber_count_are_reported(self):
        hub = LogHub(ring_size=7)
        assert hub.ring_size == 7
        assert hub.subscriber_count == 0

        unsubscribe = hub.subscribe(lambda entry: None)
        assert hub.subscriber_count == 1

        unsubscribe()
        assert hub.subscriber_count == 0


