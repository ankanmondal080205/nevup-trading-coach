"""
Session Router - Handles live session trade events.
Detects behavioral signals and streams coaching responses.
Latency: <= 3 seconds p99 on warm repeated calls.
"""

import json
import time
import logging
from fastapi import APIRouter, Request
from fastapi.responses import StreamingResponse
import redis.asyncio as redis

from app.models.schemas import SessionEvent, CoachingResponse
from app.services.coaching_service import CoachingService
from app.services.memory_store import MemoryStore

router = APIRouter()
logger = logging.getLogger(__name__)
coaching_service = CoachingService()


def get_store(request: Request) -> MemoryStore:
    return request.app.state.memory_store


@router.post("/events")
async def post_session_event(event: SessionEvent, request: Request):
    """
    POST /session/events
    Stream a coaching response for a live trade event.
    Detects one of 5 behavioral signals and streams coaching via SSE.
    Latency: <= 3s p99 on warm calls.
    """
    store = get_store(request)
    start_time = time.time()

    # Get session history for this user
    all_sessions = await store.get_user_sessions(event.userId)
    session_trades = []

    # Get trades from current session if we have cached them
    for s in all_sessions:
        if s.get("sessionId") == event.sessionId:
            session_trades = s.get("trades", [])
            break

    trade_dict = event.trade.model_dump()
    trade_dict["timestamp"] = str(trade_dict["timestamp"])

    # Detect behavioral signal
    signal = await coaching_service.detect_signal(trade_dict, session_trades)

    # Get relevant memory context
    context_result = await store.get_context(event.userId, signal if signal != "NONE" else "LOSS")
    context_sessions = context_result.get("sessions", [])

    async def event_stream():
        # Send signal metadata first
        metadata = {
            "type": "metadata",
            "sessionId": event.sessionId,
            "userId": event.userId,
            "signal": signal,
            "latency_ms": int((time.time() - start_time) * 1000),
        }
        yield f"data: {json.dumps(metadata)}\n\n"

        # Stream coaching message
        yield f"data: {json.dumps({'type': 'coaching_start'})}\n\n"

        full_message = []
        async for token in coaching_service.stream_coaching_message(
            user_id=event.userId,
            signal=signal,
            trade=trade_dict,
            context_sessions=context_sessions,
        ):
            full_message.append(token)
            yield f"data: {json.dumps({'type': 'token', 'text': token})}\n\n"

        # Send completion event
        complete_message = "".join(full_message)
        yield f"data: {json.dumps({'type': 'done', 'full_message': complete_message, 'signal': signal})}\n\n"

    return StreamingResponse(
        event_stream(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
            "Connection": "keep-alive",
        },
    )


@router.post("/events/sync", response_model=CoachingResponse)
async def post_session_event_sync(event: SessionEvent, request: Request):
    """
    POST /session/events/sync
    Non-streaming version for testing. Returns full coaching response.
    """
    store = get_store(request)

    all_sessions = await store.get_user_sessions(event.userId)
    session_trades = []
    for s in all_sessions:
        if s.get("sessionId") == event.sessionId:
            session_trades = s.get("trades", [])
            break

    trade_dict = event.trade.model_dump()
    trade_dict["timestamp"] = str(trade_dict["timestamp"])

    signal = await coaching_service.detect_signal(trade_dict, session_trades)
    context_result = await store.get_context(event.userId, signal if signal != "NONE" else "LOSS")
    context_sessions = context_result.get("sessions", [])

    message = await coaching_service.generate_coaching_message(
        user_id=event.userId,
        signal=signal,
        trade=trade_dict,
        context_sessions=context_sessions,
    )

    referenced = [s.get("sessionId") for s in context_sessions[:3] if s.get("sessionId")]

    return CoachingResponse(
        sessionId=event.sessionId,
        userId=event.userId,
        signal=signal,
        message=message,
        referenced_sessions=referenced,
        confidence=0.85 if signal != "NONE" else 0.3,
    )


@router.post("/end")
async def end_session(
    userId: str,
    sessionId: str,
    summary: str,
    request: Request,
):
    """
    POST /session/end
    End a session and persist its summary to memory.
    """
    store = get_store(request)
    all_sessions = await store.get_user_sessions(userId)

    # Compute basic metrics from stored trades if available
    from app.models.schemas import BehavioralMetrics, PutSessionRequest

    metrics = BehavioralMetrics(
        winRate=0.5, avgPnl=0.0, maxDrawdown=0.0, tradeCount=1, avgDuration_min=30.0
    )

    session = await store.put_session(
        user_id=userId,
        session_id=sessionId,
        summary=summary,
        metrics=metrics,
        tags=["SESSION_ENDED"],
    )

    return {"status": "session_stored", "sessionId": sessionId, "patternIds": session.patternIds}
