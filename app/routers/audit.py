"""
Audit Router - Anti-Hallucination Audit Endpoint

POST /audit
Accepts a coaching response body and returns each referenced sessionId
with a found | not-found flag.

Reviewers will call this endpoint to verify coaching doesn't hallucinate session references.
"""

import re
import logging
from fastapi import APIRouter, Request

from app.models.schemas import AuditRequest, AuditResponse, AuditResult
from app.services.memory_store import MemoryStore

router = APIRouter()
logger = logging.getLogger(__name__)


def get_store(request: Request) -> MemoryStore:
    return request.app.state.memory_store


@router.post("", response_model=AuditResponse)
async def audit_coaching_response(body: AuditRequest, request: Request):
    """
    POST /audit
    Accepts a coaching response body and verifies each referenced sessionId.
    Returns found | not-found for each session cited.

    Detects session IDs in two ways:
    1. From the explicitly provided referenced_session_ids list
    2. Auto-extracted from the coaching_response text (pattern: sess_*)
    """
    store = get_store(request)

    # Combine explicit IDs with auto-extracted ones from text
    session_ids_to_check = set(body.referenced_session_ids)

    # Auto-extract session IDs from the coaching response text
    extracted = _extract_session_ids(body.coaching_response)
    session_ids_to_check.update(extracted)

    if not session_ids_to_check:
        return AuditResponse(
            audit_results=[],
            total_referenced=0,
            found_count=0,
            hallucination_rate=0.0,
        )

    audit_results = []
    found_count = 0

    for session_id in session_ids_to_check:
        found, user_id = await store.session_exists(session_id)
        audit_results.append(
            AuditResult(sessionId=session_id, found=found, userId=user_id)
        )
        if found:
            found_count += 1

    total = len(audit_results)
    hallucination_rate = (total - found_count) / total if total > 0 else 0.0

    logger.info(
        f"Audit complete: {found_count}/{total} sessions found. "
        f"Hallucination rate: {hallucination_rate:.2%}"
    )

    return AuditResponse(
        audit_results=audit_results,
        total_referenced=total,
        found_count=found_count,
        hallucination_rate=hallucination_rate,
    )


def _extract_session_ids(text: str) -> set[str]:
    """
    Extract session IDs from coaching response text.
    Looks for patterns like: sess_001_a, sess_xxx_yyy, session IDs in quotes.
    """
    patterns = [
        r'\bsess_[a-zA-Z0-9_]+\b',           # sess_001_a format
        r'\bsession[_\s]([a-zA-Z0-9_]+)\b',   # "session abc123"
        r'"([a-zA-Z0-9_-]{6,})"',              # quoted IDs
    ]

    ids = set()
    for pattern in patterns:
        matches = re.findall(pattern, text, re.IGNORECASE)
        ids.update(matches)

    return ids
