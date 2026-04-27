"""
NevUp Hackathon 2026 - Track 2: System of AI Engine
Stateful Trading Psychology Coach
"""

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from contextlib import asynccontextmanager
import logging

from app.routers import memory, session, audit, profile, eval_router
from app.services.memory_store import MemoryStore

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Initialize memory store on startup."""
    logger.info("Initializing Trading Psychology Coach...")
    store = MemoryStore()
    await store.initialize()
    app.state.memory_store = store
    logger.info("Memory store initialized successfully.")
    yield
    logger.info("Shutting down...")


app = FastAPI(
    title="NevUp Trading Psychology Coach",
    description="Stateful AI trading psychology coach with verifiable memory and reproducible evaluation",
    version="1.0.0",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Include routers
app.include_router(memory.router, prefix="/memory", tags=["memory"])
app.include_router(session.router, prefix="/session", tags=["session"])
app.include_router(audit.router, prefix="/audit", tags=["audit"])
app.include_router(profile.router, prefix="/profile", tags=["profile"])
app.include_router(eval_router.router, prefix="/eval", tags=["evaluation"])


@app.get("/health")
async def health_check():
    return {"status": "healthy", "service": "trading-psychology-coach"}


@app.get("/")
async def root():
    return {
        "service": "NevUp Trading Psychology Coach",
        "version": "1.0.0",
        "track": "Track 2 - System of AI Engine",
        "endpoints": {
            "memory": "/memory/{userId}/sessions/{sessionId}",
            "context": "/memory/{userId}/context",
            "session_events": "/session/events",
            "audit": "/audit",
            "profile": "/profile/{userId}",
            "eval": "/eval/run",
        }
    }
