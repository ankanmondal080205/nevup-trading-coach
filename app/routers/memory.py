"""
Memory Router - Exposes all 3 operations from the Memory Contract:

1. PUT /memory/{userId}/sessions/{sessionId}  - Persist session summary
2. GET /memory/{userId}/context?relevantTo={signal} - Query memory for context
3. GET /memory/{userId}/sessions/{sessionId} - Retrieve specific session for audit
"""

from fastapi import APIRouter, Request, HTTPException
from app.models.schemas import PutSessionRequest, MemoryQueryResponse, SessionSummary
from app.services.memory_store import MemoryStore

router = APIRouter()


def get_store(request: Request) -> MemoryStore:
    return request.app.state.memory_store


@router.put("/{userId}/sessions/{sessionId}", response_model=SessionSummary)
async def put_session(
    userId: str,
    sessionId: str,
    body: PutSessionRequest,
    request: Request,
):
    """
    Persist a session summary after a session ends.
    Body: { summary: string, metrics: BehavioralMetrics, tags: string[] }
    """
    store = get_store(request)
    session = await store.put_session(
        user_id=userId,
        session_id=sessionId,
        summary=body.summary,
        metrics=body.metrics,
        tags=body.tags,
    )
    return session


@router.get("/{userId}/context", response_model=MemoryQueryResponse)
async def get_context(
    userId: str,
    relevantTo: str,
    request: Request,
):
    """
    Query memory for context before generating a coaching message.
    Response: { sessions: SessionSummary[], patternIds: string[] }
    """
    store = get_store(request)
    result = await store.get_context(user_id=userId, relevant_to=relevantTo)
    return result


@router.get("/{userId}/sessions/{sessionId}")
async def get_session(
    userId: str,
    sessionId: str,
    request: Request,
):
    """
    Retrieve a specific session for hallucination audit.
    Response: raw session record (must exactly match what was stored)
    """
    store = get_store(request)
    session = await store.get_session(user_id=userId, session_id=sessionId)
    if session is None:
        raise HTTPException(status_code=404, detail=f"Session {sessionId} not found for user {userId}")
    return session
