"""Agent observability + SSE endpoints (Phase 6)."""

from __future__ import annotations

import asyncio
import queue
from typing import Optional
from uuid import UUID

from fastapi import APIRouter, Header, Path, Query, Request
from fastapi.responses import StreamingResponse

from app.schemas.agent_event import AgentEventType
from app.schemas.common import success_response
from app.services.agent_event_service import (
    HEARTBEAT_INTERVAL_S,
    agent_event_service,
)

router = APIRouter(prefix="/runs", tags=["agents-events"])


@router.get("/{run_id}/agents")
def list_agents(run_id: UUID = Path(...)) -> dict:
    result = agent_event_service.list_agents(run_id)
    return success_response(
        data=result.model_dump(mode="json"),
        message="Agents retrieved",
    )


@router.get("/{run_id}/agents/status")
def agents_status(run_id: UUID = Path(...)) -> dict:
    result = agent_event_service.get_agents_status(run_id)
    return success_response(
        data=result.model_dump(mode="json"),
        message="Agents status retrieved",
    )


@router.get("/{run_id}/agents/{agent_name}")
def get_agent(
    run_id: UUID = Path(...),
    agent_name: str = Path(..., min_length=1, max_length=100),
) -> dict:
    result = agent_event_service.get_agent(run_id, agent_name)
    return success_response(
        data=result.model_dump(mode="json"),
        message="Agent retrieved",
    )


@router.get("/{run_id}/events")
def list_events(
    run_id: UUID = Path(...),
    event_type: Optional[AgentEventType] = Query(default=None),
    agent_name: Optional[str] = Query(default=None, max_length=100),
    after_id: Optional[UUID] = Query(
        default=None,
        description="Return events strictly after this event id (replay cursor).",
    ),
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
) -> dict:
    result = agent_event_service.list_events(
        run_id,
        event_type=event_type,
        agent_name=agent_name,
        after_id=after_id,
        limit=limit,
        offset=offset,
    )
    return success_response(
        data=result.model_dump(mode="json"),
        message="Events retrieved",
    )


@router.get("/{run_id}/stream")
async def stream_events(
    request: Request,
    run_id: UUID = Path(...),
    after_id: Optional[UUID] = Query(
        default=None,
        description="Replay/live cursor; also accepts Last-Event-ID header.",
    ),
    replay: bool = Query(
        default=True,
        description="If true, replay stored events before live tail.",
    ),
    last_event_id: Optional[str] = Header(default=None, alias="Last-Event-ID"),
) -> StreamingResponse:
    cursor = after_id
    if cursor is None and last_event_id:
        try:
            cursor = UUID(last_event_id.strip())
        except ValueError:
            cursor = None

    # Authorize early (raises AppError if missing).
    agent_event_service._authorized_run(run_id, user_id=None)

    async def event_generator():
        seen: set[UUID] = set()
        last_seen = cursor
        live_q = agent_event_service._bus.subscribe(run_id)
        last_heartbeat = asyncio.get_event_loop().time()

        try:
            if replay:
                page, _ = agent_event_service._events.list_for_run(
                    run_id, after_id=cursor, limit=200, offset=0
                )
                for event in page:
                    if await request.is_disconnected():
                        return
                    if event.id in seen:
                        continue
                    seen.add(event.id)
                    last_seen = event.id
                    yield agent_event_service._format_sse(event)

            while True:
                if await request.is_disconnected():
                    return
                try:
                    event = await asyncio.to_thread(live_q.get, True, 1.0)
                except queue.Empty:
                    now = asyncio.get_event_loop().time()
                    if now - last_heartbeat >= HEARTBEAT_INTERVAL_S:
                        yield ": heartbeat\n\n"
                        last_heartbeat = now
                    continue

                if event.id in seen:
                    continue
                if last_seen is not None and event.id == last_seen:
                    continue
                seen.add(event.id)
                last_seen = event.id
                yield agent_event_service._format_sse(event)
                last_heartbeat = asyncio.get_event_loop().time()
        finally:
            agent_event_service._bus.unsubscribe(run_id, live_q)

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )
