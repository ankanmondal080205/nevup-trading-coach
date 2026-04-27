"""
Data models for the Trading Psychology Coach
Aligned with nevup_seed_dataset.json schema v1.0.0
"""

from pydantic import BaseModel, Field
from typing import Optional, Literal
from datetime import datetime
import uuid


PathologyLabel = Literal[
    "revenge_trading", "overtrading", "fomo_entries", "plan_non_adherence",
    "premature_exit", "loss_running", "session_tilt", "time_of_day_bias",
    "position_sizing_inconsistency",
]

EmotionalState = Literal["calm", "anxious", "greedy", "fearful", "neutral"]
AssetClass = Literal["equity", "crypto", "forex"]
Direction = Literal["long", "short"]


class BehavioralMetrics(BaseModel):
    winRate: float = Field(..., ge=0.0, le=1.0)
    avgPnl: float
    maxDrawdown: float
    tradeCount: int = Field(..., ge=1)
    avgDuration_min: float = Field(..., ge=0)
    avgPlanAdherence: Optional[float] = None
    dominantEmotionalState: Optional[str] = None


class SessionSummary(BaseModel):
    sessionId: str
    userId: str
    summary: str
    metrics: BehavioralMetrics
    tags: list[str] = Field(default_factory=list)
    timestamp: datetime = Field(default_factory=datetime.utcnow)
    patternIds: list[str] = Field(default_factory=list)


class Trade(BaseModel):
    tradeId: str = Field(default_factory=lambda: str(uuid.uuid4()))
    userId: Optional[str] = None
    sessionId: Optional[str] = None
    asset: str
    assetClass: AssetClass = "equity"
    direction: Direction = "long"
    entryPrice: float = Field(..., gt=0)
    exitPrice: Optional[float] = None
    quantity: float = Field(..., gt=0)
    entryAt: Optional[str] = None
    exitAt: Optional[str] = None
    status: str = "closed"
    outcome: Optional[Literal["win", "loss"]] = None
    pnl: Optional[float] = None
    planAdherence: Optional[int] = Field(None, ge=1, le=5)
    emotionalState: Optional[EmotionalState] = None
    entryRationale: Optional[str] = None
    revengeFlag: bool = False


class SessionEvent(BaseModel):
    userId: str
    sessionId: str = Field(default_factory=lambda: str(uuid.uuid4()))
    trade: Trade
    context: Optional[str] = None


class CoachingResponse(BaseModel):
    sessionId: str
    userId: str
    signal: str
    message: str
    referenced_sessions: list[str] = Field(default_factory=list)
    confidence: float = Field(..., ge=0.0, le=1.0)
    timestamp: datetime = Field(default_factory=datetime.utcnow)


class AuditRequest(BaseModel):
    coaching_response: str
    referenced_session_ids: list[str]


class AuditResult(BaseModel):
    sessionId: str
    found: bool
    userId: Optional[str] = None


class AuditResponse(BaseModel):
    audit_results: list[AuditResult]
    total_referenced: int
    found_count: int
    hallucination_rate: float


class BehavioralProfile(BaseModel):
    userId: str
    name: Optional[str] = None
    pathology_labels: list[str] = Field(default_factory=list)
    weakness_patterns: list[dict] = Field(default_factory=list)
    failure_mode: str = ""
    peak_window: str = ""
    session_count: int = 0
    overall_win_rate: float = 0.0
    avg_session_pnl: float = 0.0
    avg_plan_adherence: float = 0.0
    derived_from_sessions: list[str] = Field(default_factory=list)
    generated_at: datetime = Field(default_factory=datetime.utcnow)


class MemoryQueryResponse(BaseModel):
    sessions: list[SessionSummary]
    patternIds: list[str]


class PutSessionRequest(BaseModel):
    summary: str
    metrics: BehavioralMetrics
    tags: list[str] = Field(default_factory=list)
