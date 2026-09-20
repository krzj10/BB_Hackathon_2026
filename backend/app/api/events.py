"""Basic live-event WebSocket (B04 scope of plan §9).

Streams canonical EventEnvelope JSON from the durable outbox to an
authenticated local client: the caller must present a session id (header or
``session_id`` query param - browsers cannot set WS headers, so both are
accepted). The stream resumes from an ``after=<outbox seq>`` cursor and never
replays spoken notifications by itself - reconnect replay/delivery hardening
is A07/B05; REST snapshots stay authoritative."""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timezone

from fastapi import APIRouter, WebSocket, WebSocketDisconnect

from ..contracts.domain import EventEnvelope, HeartbeatPayload
from .security import SESSION_HEADER

logger = logging.getLogger("eva.api.events")

router = APIRouter(prefix="/api/events", tags=["events"])

_POLL_INTERVAL_SECONDS = 1.0
_HEARTBEAT_SECONDS = 15.0


@router.websocket("")
async def stream_events(websocket: WebSocket) -> None:
    session_id = (
        websocket.query_params.get("session_id")
        or websocket.headers.get(SESSION_HEADER)
        or ""
    ).strip()
    if not session_id:
        # 4401: authentication required (custom WS close code).
        await websocket.close(code=4401)
        return

    outbox = getattr(websocket.app.state, "event_outbox_repository", None)
    if outbox is None:
        await websocket.close(code=1011)
        return

    try:
        after = int(websocket.query_params.get("after") or outbox.max_seq())
    except (TypeError, ValueError):
        after = 0

    await websocket.accept()
    last_heartbeat = asyncio.get_event_loop().time()
    try:
        while True:
            rows = outbox.list_after(after, limit=100)
            for seq, envelope in rows:
                await websocket.send_json(envelope.model_dump(mode="json"))
                after = max(after, seq)
            now_mono = asyncio.get_event_loop().time()
            if now_mono - last_heartbeat >= _HEARTBEAT_SECONDS:
                heartbeat = EventEnvelope(
                    event_id=f"evt-heartbeat-{datetime.now(timezone.utc).timestamp()}",
                    sequence=0,
                    session_id=session_id,
                    occurred_at=datetime.now(timezone.utc),
                    type="heartbeat",
                    payload=HeartbeatPayload(server_time=datetime.now(timezone.utc)),
                )
                await websocket.send_json(heartbeat.model_dump(mode="json"))
                last_heartbeat = now_mono
            await asyncio.sleep(_POLL_INTERVAL_SECONDS)
    except WebSocketDisconnect:
        return
    except Exception as exc:  # sanitized; the client reconnects with its cursor
        logger.error("live-event stream failed (%s)", type(exc).__name__)
        try:
            await websocket.close(code=1011)
        except Exception:
            pass
