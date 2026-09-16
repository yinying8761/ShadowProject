"""
Debug log channel — `/ws/logs` (ADR-0002).

Outbound: the ring snapshot on connect (``logs_history``), then every entry
added afterwards as it happens (``logs_line``).  Inbound: renderer error
reports (``renderer_log``), framed here as ``source=renderer`` entries, and
debug-console commands (ticket 04).

The hub comes from ``app.state.log_hub``, installed by main.py\'s lifespan.
"""

from __future__ import annotations

import asyncio

from fastapi import APIRouter, WebSocket, WebSocketDisconnect

from services.log_hub import LogHub, RENDERER

router = APIRouter()

# A panel that stops reading must not grow the queue without bound: past this
# the oldest pending lines are dropped so the newest ones still arrive.
MAX_PENDING_LINES = 1000


def _enqueue(pending: "asyncio.Queue[dict]", entry: dict) -> None:
    """Queue one entry for the socket, dropping the oldest if we are behind."""
    while pending.qsize() >= MAX_PENDING_LINES:
        try:
            pending.get_nowait()
        except asyncio.QueueEmpty:
            break
    pending.put_nowait(entry)


@router.websocket("/ws/logs")
async def ws_logs(websocket: WebSocket) -> None:
    hub: LogHub = websocket.app.state.log_hub
    await websocket.accept()

    loop = asyncio.get_running_loop()
    pending: "asyncio.Queue[dict]" = asyncio.Queue()

    def on_entry(entry: dict) -> None:
        # Entries are produced on whatever thread printed, so hop back to the
        # socket\'s loop before touching the queue (log_hub documents this).
        loop.call_soon_threadsafe(_enqueue, pending, entry)

    # Subscribe before snapshotting: a line printed in between then shows up in
    # both places, which beats losing it.  Both are quick and happen once.
    unsubscribe = hub.subscribe(on_entry)
    history = hub.lines()

    async def pump() -> None:
        while True:
            await websocket.send_json({"type": "logs_line", "line": await pending.get()})

    async def listen() -> None:
        while True:
            message = await websocket.receive_json()
            if message.get("type") == "renderer_log":
                hub.add(
                    str(message.get("message", "")),
                    source=RENDERER,
                    level=str(message.get("level", "error")),
                )

    tasks = [asyncio.create_task(pump()), asyncio.create_task(listen())]
    try:
        await websocket.send_json({"type": "logs_history", "lines": history})
        # Whichever direction ends first (client closed, send failed) ends both.
        await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
    except WebSocketDisconnect:
        pass
    finally:
        unsubscribe()
        for task in tasks:
            task.cancel()
