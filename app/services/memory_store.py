"""
Memory Store - Redis with AOF persistence.
Survives docker compose restart without data loss (named volume + appendonly yes).
"""

import json
import logging
from datetime import datetime
from typing import Optional
import redis.asyncio as redis
import os

from app.models.schemas import SessionSummary, BehavioralMetrics

logger = logging.getLogger(__name__)
REDIS_URL = os.getenv("REDIS_URL", "redis://redis:6379")


class MemoryStore:
    def __init__(self):
        self.redis: Optional[redis.Redis] = None

    async def initialize(self):
        self.redis = redis.from_url(REDIS_URL, decode_responses=True)
        await self.redis.ping()
        logger.info(f"Connected to Redis at {REDIS_URL}")

    async def put_session(
        self, user_id: str, session_id: str, summary: str,
        metrics: BehavioralMetrics, tags: list[str]
    ) -> SessionSummary:
        """PUT /memory/{userId}/sessions/{sessionId}"""
        session = SessionSummary(
            sessionId=session_id,
            userId=user_id,
            summary=summary,
            metrics=metrics,
            tags=tags,
            timestamp=datetime.utcnow(),
            patternIds=self._derive_pattern_ids(tags, metrics),
        )
        key = f"memory:{user_id}:sessions:{session_id}"
        await self.redis.set(key, session.model_dump_json())
        await self.redis.sadd(f"memory:{user_id}:session_index", session_id)
        for tag in session.patternIds:
            await self.redis.sadd(f"memory:{user_id}:patterns:{tag}", session_id)
        logger.info(f"Stored session {session_id} for user {user_id}")
        return session

    async def get_context(self, user_id: str, relevant_to: str) -> dict:
        """GET /memory/{userId}/context?relevantTo={signal}"""
        session_ids = await self.redis.smembers(f"memory:{user_id}:session_index")
        if not session_ids:
            return {"sessions": [], "patternIds": []}

        sessions = []
        all_patterns = set()
        for sid in session_ids:
            data = await self.redis.get(f"memory:{user_id}:sessions:{sid}")
            if data:
                s = SessionSummary.model_validate_json(data)
                if self._is_relevant(s, relevant_to):
                    sessions.append(s)
                    all_patterns.update(s.patternIds)

        sessions.sort(key=lambda s: s.timestamp, reverse=True)
        return {
            "sessions": [s.model_dump() for s in sessions[:5]],
            "patternIds": list(all_patterns),
        }

    async def get_session(self, user_id: str, session_id: str) -> Optional[dict]:
        """GET /memory/{userId}/sessions/{sessionId} — raw record for hallucination audit"""
        data = await self.redis.get(f"memory:{user_id}:sessions:{session_id}")
        return json.loads(data) if data else None

    async def session_exists(self, session_id: str) -> tuple[bool, Optional[str]]:
        """Cross-user lookup for hallucination audit."""
        async for key in self.redis.scan_iter(f"memory:*:sessions:{session_id}"):
            user_id = key.split(":")[1]
            return True, user_id
        return False, None

    async def get_user_sessions(self, user_id: str) -> list[dict]:
        sids = await self.redis.smembers(f"memory:{user_id}:session_index")
        sessions = []
        for sid in sids:
            data = await self.redis.get(f"memory:{user_id}:sessions:{sid}")
            if data:
                sessions.append(json.loads(data))
        sessions.sort(key=lambda s: s.get("timestamp", ""), reverse=True)
        return sessions

    def _derive_pattern_ids(self, tags: list[str], metrics: BehavioralMetrics) -> list[str]:
        patterns = set(tags)
        if metrics.winRate < 0.3:
            patterns.add("low_win_rate")
        if metrics.maxDrawdown < -500:
            patterns.add("high_drawdown")
        if metrics.avgDuration_min < 15:
            patterns.add("short_hold_time")
        if metrics.tradeCount > 10:
            patterns.add("overtrading")
        if metrics.avgPlanAdherence and metrics.avgPlanAdherence < 2.5:
            patterns.add("plan_non_adherence")
        return list(patterns)

    def _is_relevant(self, session: SessionSummary, signal: str) -> bool:
        blob = " ".join(session.tags + session.patternIds + [session.summary]).lower()
        signal_lower = signal.lower().replace("_", " ")
        if signal.lower() in blob or signal_lower in blob:
            return True
        # Semantic groupings
        groups = {
            "revenge": ["revenge_trading", "has_revenge_flags"],
            "overtrad": ["overtrading"],
            "fomo": ["fomo_entries"],
            "plan": ["plan_non_adherence", "low_plan_adherence"],
            "premature": ["premature_exit"],
            "loss_run": ["loss_running"],
            "tilt": ["session_tilt"],
            "time": ["time_of_day_bias"],
            "sizing": ["position_sizing_inconsistency"],
        }
        for key, related in groups.items():
            if key in signal.lower():
                return any(r in blob for r in related)
        return False
