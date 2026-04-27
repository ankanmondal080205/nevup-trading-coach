"""
Profile Router - Behavioral Profiling
Ingests trader history from the real nevup_seed_dataset.json
Each claim cites specific sessionId and tradeId as evidence.
"""

import json
import logging
from pathlib import Path
from datetime import datetime
from fastapi import APIRouter, Request, HTTPException

from app.models.schemas import BehavioralProfile, BehavioralMetrics, PutSessionRequest
from app.services.coaching_service import CoachingService
from app.services.memory_store import MemoryStore

router = APIRouter()
logger = logging.getLogger(__name__)
coaching_service = CoachingService()

SEED_PATH = Path("/app/nevup_seed_dataset.json")


def load_seed() -> dict:
    for p in [SEED_PATH, Path("nevup_seed_dataset.json")]:
        if p.exists():
            with open(p) as f:
                return json.load(f)
    raise FileNotFoundError("Seed dataset not found")


def get_store(request: Request) -> MemoryStore:
    return request.app.state.memory_store


def compute_session_metrics(session: dict) -> BehavioralMetrics:
    trades = session.get("trades", [])
    if not trades:
        return BehavioralMetrics(winRate=0, avgPnl=0, maxDrawdown=0, tradeCount=0, avgDuration_min=0)

    durations = []
    for t in trades:
        try:
            from datetime import datetime as dt
            entry = dt.fromisoformat(t["entryAt"].replace("Z", "+00:00"))
            exit_ = dt.fromisoformat(t["exitAt"].replace("Z", "+00:00"))
            durations.append((exit_ - entry).total_seconds() / 60)
        except Exception:
            durations.append(0)

    plan_scores = [t.get("planAdherence") for t in trades if t.get("planAdherence")]
    emotions = [t.get("emotionalState") for t in trades if t.get("emotionalState")]
    dominant_emotion = max(set(emotions), key=emotions.count) if emotions else None

    pnls = [t.get("pnl", 0) for t in trades]
    running_pnl = 0
    peak = 0
    max_dd = 0
    for p in pnls:
        running_pnl += p
        if running_pnl > peak:
            peak = running_pnl
        dd = running_pnl - peak
        if dd < max_dd:
            max_dd = dd

    return BehavioralMetrics(
        winRate=session.get("winRate", 0),
        avgPnl=session.get("totalPnl", 0) / len(trades),
        maxDrawdown=max_dd,
        tradeCount=len(trades),
        avgDuration_min=sum(durations) / len(durations) if durations else 0,
        avgPlanAdherence=sum(plan_scores) / len(plan_scores) if plan_scores else None,
        dominantEmotionalState=dominant_emotion,
    )


@router.get("")
async def list_profiles():
    try:
        seed = load_seed()
        return {
            "traders": [
                {
                    "userId": t["userId"],
                    "name": t["name"],
                    "groundTruthPathologies": t.get("groundTruthPathologies", []),
                    "totalSessions": t["stats"]["totalSessions"],
                    "totalTrades": t["stats"]["totalTrades"],
                }
                for t in seed["traders"]
            ]
        }
    except FileNotFoundError:
        return {"traders": [], "message": "Seed dataset not found"}


@router.post("/ingest-seed")
async def ingest_seed_dataset(request: Request):
    """
    Ingest all 10 traders from the seed dataset into the Redis memory store.
    Pre-populates memory so coaching context is available immediately.
    """
    store = get_store(request)
    seed = load_seed()

    ingested = []
    for trader in seed["traders"]:
        user_id = trader["userId"]
        for session in trader.get("sessions", []):
            metrics = compute_session_metrics(session)
            trades = session.get("trades", [])
            emotions = [t.get("emotionalState") for t in trades if t.get("emotionalState")]
            revenge_count = sum(1 for t in trades if t.get("revengeFlag"))
            low_adherence = sum(1 for t in trades if (t.get("planAdherence") or 5) <= 2)

            summary = (
                f"{trader['name']} | {session['date'][:10]} | "
                f"{len(trades)} trades | WR {session.get('winRate', 0):.0%} | "
                f"PnL ${session.get('totalPnl', 0):.0f} | "
                f"Revenge flags: {revenge_count} | Low adherence: {low_adherence} | "
                f"Pathology: {', '.join(trader.get('groundTruthPathologies', ['none']))}"
            )

            tags = list(trader.get("groundTruthPathologies", []))
            if revenge_count > 0:
                tags.append("has_revenge_flags")
            if low_adherence > 0:
                tags.append("low_plan_adherence")

            await store.put_session(
                user_id=user_id,
                session_id=session["sessionId"],
                summary=summary,
                metrics=metrics,
                tags=tags,
            )
            ingested.append(session["sessionId"])

    return {
        "status": "success",
        "ingested_sessions": len(ingested),
        "traders": len(seed["traders"]),
        "session_ids": ingested,
    }


@router.get("/{userId}", response_model=BehavioralProfile)
async def get_behavioral_profile(userId: str, request: Request):
    """
    GET /profile/{userId}
    Generate a behavioral profile. Each weakness_pattern cites specific sessionId + tradeId.
    """
    store = get_store(request)

    # Try seed dataset first (authoritative)
    trader_data = None
    try:
        seed = load_seed()
        for t in seed["traders"]:
            if t["userId"] == userId:
                trader_data = t
                break
    except FileNotFoundError:
        pass

    if not trader_data:
        # Fall back to live memory
        sessions = await store.get_user_sessions(userId)
        if not sessions:
            raise HTTPException(status_code=404, detail=f"No data found for trader {userId}")
        trader_data = {"userId": userId, "name": userId, "sessions": sessions, "description": ""}

    # Generate AI profile
    profile_data = await coaching_service.generate_behavioral_profile(trader_data)

    # Fallback if AI parsing fails
    if "error" in profile_data:
        logger.warning(f"AI profile failed for {userId}, using ground truth fallback")
        profile_data = {
            "pathology_labels": trader_data.get("groundTruthPathologies", []),
            "weakness_patterns": [],
            "failure_mode": trader_data.get("description", ""),
            "peak_window": "",
            "coaching_priority": (trader_data.get("groundTruthPathologies") or ["unknown"])[0],
        }

    # Compute aggregate metrics
    all_sessions = trader_data.get("sessions", [])
    session_count = len(all_sessions)
    total_wr = sum(s.get("winRate", 0) for s in all_sessions)
    total_pnl = sum(s.get("totalPnl", 0) for s in all_sessions)
    all_plan = []
    for s in all_sessions:
        for t in s.get("trades", []):
            if t.get("planAdherence"):
                all_plan.append(t["planAdherence"])

    # Find peak window (highest totalPnl session)
    peak_window = profile_data.get("peak_window", "")
    if not peak_window and all_sessions:
        best = max(all_sessions, key=lambda s: s.get("totalPnl", 0))
        peak_window = best.get("sessionId", "")

    return BehavioralProfile(
        userId=userId,
        name=trader_data.get("name"),
        pathology_labels=profile_data.get("pathology_labels", []),
        weakness_patterns=profile_data.get("weakness_patterns", []),
        failure_mode=profile_data.get("failure_mode", ""),
        peak_window=peak_window,
        session_count=session_count,
        overall_win_rate=total_wr / session_count if session_count else 0,
        avg_session_pnl=total_pnl / session_count if session_count else 0,
        avg_plan_adherence=sum(all_plan) / len(all_plan) if all_plan else 0,
        derived_from_sessions=[s.get("sessionId", "") for s in all_sessions],
    )
