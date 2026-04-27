# NevUp Hackathon 2026 — Track 2: System of AI Engine

Stateful trading psychology coach with verifiable memory, anti-hallucination audit, and reproducible evaluation.

---

## Quick Start

```bash
# 1. Set your API key
export ANTHROPIC_API_KEY=your_key_here

# 2. Start everything — single command, no manual steps
docker compose up

# 3. Verify
curl http://localhost:8000/health

# 4. Pre-load the 10 seed traders into memory
curl -X POST http://localhost:8000/profile/ingest-seed
```

Interactive docs: **http://localhost:8000/docs**

---

## Architecture

```
POST /session/events  →  Signal Detection (revenge_trading, overtrading, fomo_entries…)
                              ↓
                        Redis Memory Query  ←→  Redis (AOF + named volume, survives restarts)
                              ↓
                        Streaming Coaching (SSE, token-by-token, <400ms first token)

PUT/GET /memory/{userId}/sessions/{sessionId}   — persist & retrieve sessions
GET     /memory/{userId}/context?relevantTo=…   — query relevant context
POST    /audit                                   — hallucination audit (LLM-free)
GET     /profile/{userId}                        — behavioral profiling
POST    /eval/run                                — evaluation harness
```

---

## Memory Contract

### 1 — Persist a session
```bash
curl -X PUT http://localhost:8000/memory/f412f236-4edc-47a2-8f54-8763a6ed2ce8/sessions/4f39c2ea-8687-41f7-85a0-1fafd3e976df \
  -H "Content-Type: application/json" \
  -d '{
    "summary": "Alex Mercer. 5 trades, 3 revenge-flagged. Opened new positions within 90s of losses in anxious state.",
    "metrics": {
      "winRate": 0.2,
      "avgPnl": -52.6,
      "maxDrawdown": -310.0,
      "tradeCount": 5,
      "avgDuration_min": 48.0,
      "avgPlanAdherence": 2.1
    },
    "tags": ["revenge_trading", "has_revenge_flags"]
  }'
```

### 2 — Query context before coaching
```bash
curl "http://localhost:8000/memory/f412f236-4edc-47a2-8f54-8763a6ed2ce8/context?relevantTo=revenge_trading"
```

### 3 — Retrieve raw session (hallucination audit)
```bash
curl http://localhost:8000/memory/f412f236-4edc-47a2-8f54-8763a6ed2ce8/sessions/4f39c2ea-8687-41f7-85a0-1fafd3e976df
```

---

## Streaming Session Events (SSE)

```bash
curl -N -X POST http://localhost:8000/session/events \
  -H "Content-Type: application/json" \
  -d '{
    "userId": "f412f236-4edc-47a2-8f54-8763a6ed2ce8",
    "sessionId": "4f39c2ea-8687-41f7-85a0-1fafd3e976df",
    "trade": {
      "asset": "NVDA",
      "assetClass": "equity",
      "direction": "long",
      "entryPrice": 483.34,
      "exitPrice": 470.53,
      "quantity": 46,
      "outcome": "loss",
      "pnl": -64.05,
      "planAdherence": 1,
      "emotionalState": "anxious",
      "entryRationale": "Trying to recover fast",
      "revengeFlag": true
    }
  }'
```

SSE stream:
```
data: {"type":"metadata","signal":"revenge_trading","latency_ms":183}
data: {"type":"coaching_start"}
data: {"type":"token","text":"I"}
data: {"type":"token","text":" notice"}
...
data: {"type":"done","full_message":"I notice a revenge trading pattern..."}
```

Non-streaming (for testing):
```bash
curl -X POST http://localhost:8000/session/events/sync \
  -H "Content-Type: application/json" \
  -d '{"userId":"f412f236-4edc-47a2-8f54-8763a6ed2ce8","sessionId":"4f39c2ea-8687-41f7-85a0-1fafd3e976df","trade":{"asset":"NVDA","assetClass":"equity","direction":"long","entryPrice":483.34,"quantity":46,"revengeFlag":true,"emotionalState":"anxious","planAdherence":1}}'
```

---

## Hallucination Audit Endpoint

```bash
# Real session IDs → found. Fake ID → not-found (hallucination caught)
curl -X POST http://localhost:8000/audit \
  -H "Content-Type: application/json" \
  -d '{
    "coaching_response": "Based on session 4f39c2ea-8687-41f7-85a0-1fafd3e976df you showed revenge_trading. Session 00000000-fake-id also showed issues.",
    "referenced_session_ids": [
      "4f39c2ea-8687-41f7-85a0-1fafd3e976df",
      "00000000-0000-0000-0000-000000000000"
    ]
  }'
```

Response:
```json
{
  "audit_results": [
    {"sessionId": "4f39c2ea-8687-41f7-85a0-1fafd3e976df", "found": true,  "userId": "f412f236-..."},
    {"sessionId": "00000000-0000-0000-0000-000000000000", "found": false, "userId": null}
  ],
  "total_referenced": 2,
  "found_count": 1,
  "hallucination_rate": 0.5
}
```

The audit endpoint is **LLM-free** — checks actual Redis keys only.

---

## Behavioral Profiling

```bash
# List all 10 seed traders
curl http://localhost:8000/profile

# Get profile for Alex Mercer (revenge_trading)
curl http://localhost:8000/profile/f412f236-4edc-47a2-8f54-8763a6ed2ce8

# Get profile for Jordan Lee (overtrading)
curl http://localhost:8000/profile/fcd434aa-2201-4060-aeb2-f44c77aa0683
```

---

## Evaluation Harness

```bash
# Via API
curl -X POST http://localhost:8000/eval/run | python3 -m json.tool

# Via script (reviewers run this from scratch)
pip install httpx
python eval/run_eval.py --api-url http://localhost:8000 --output eval_report.json
cat eval_report.json
```

---

## Memory Persistence Test

```bash
# Store a session
curl -X PUT http://localhost:8000/memory/test_user/sessions/test_sess \
  -H "Content-Type: application/json" \
  -d '{"summary":"persistence test","metrics":{"winRate":0.5,"avgPnl":0,"maxDrawdown":-100,"tradeCount":1,"avgDuration_min":30},"tags":[]}'

# Restart the stack
docker compose restart && sleep 15

# Session must still exist (not 404)
curl http://localhost:8000/memory/test_user/sessions/test_sess
```

---

## Pathology Labels (from seed dataset)

| Label | Description |
|---|---|
| `revenge_trading` | New trade opened within seconds of a loss in anxious/fearful state |
| `overtrading` | Excessive trade count far beyond baseline |
| `fomo_entries` | Impulsive entry driven by fear of missing a move |
| `plan_non_adherence` | Consistently low planAdherence scores (1–2) |
| `premature_exit` | Winning positions closed too early |
| `loss_running` | Losing positions held past stop |
| `session_tilt` | Performance deteriorates as session progresses |
| `time_of_day_bias` | Significantly different P&L at specific times of day |
| `position_sizing_inconsistency` | Position sizes vary dramatically with no logic |

---

## Submission Checklist

- ✅ Live API endpoint (`docker compose up`)
- ✅ Public GitHub repository
- ✅ Evaluation script `eval/run_eval.py` + classification report (JSON/HTML)
- ✅ `DECISIONS.md` with architectural rationale
- ✅ Hallucination audit demonstrated via curl in this README
- ✅ `docker-compose.yml` — single `docker compose up`, no manual steps

---

## Project Structure

```
nevup-trading-coach/
├── app/
│   ├── main.py
│   ├── models/schemas.py          # Pydantic models aligned with seed dataset schema
│   ├── routers/
│   │   ├── memory.py              # Memory contract (3 endpoints)
│   │   ├── session.py             # Live events + SSE streaming
│   │   ├── audit.py               # Anti-hallucination (LLM-free)
│   │   ├── profile.py             # Behavioral profiling
│   │   └── eval_router.py         # Evaluation harness
│   └── services/
│       ├── memory_store.py        # Redis AOF persistence
│       └── coaching_service.py    # Claude integration + streaming
├── eval/
│   └── run_eval.py                # Standalone evaluation script
├── nevup_seed_dataset.json        # Official seed dataset (as-is, not modified)
├── Dockerfile
├── docker-compose.yml
├── requirements.txt
├── DECISIONS.md
└── README.md
```
