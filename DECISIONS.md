# DECISIONS.md - Architectural Rationale

## NevUp Hackathon 2026 - Track 2: System of AI Engine

---

## Core Architecture

### Memory Layer: Redis with Append-Only Persistence

**Decision**: Use Redis with `appendonly yes` and a named Docker volume.

**Rationale**:
- Plain Python dicts reset on restart → disqualified by Hard Requirement #4
- Non-persisted Redis (default) resets on restart → disqualified
- Redis with `appendonly yes` + named volume survives `docker compose restart` with zero data loss
- Named volumes (`redis_data`) are managed by Docker and persist independently of container lifecycle
- Redis `SAVE 60 1` additionally writes RDB snapshots every 60 seconds

**Key evidence**: `docker-compose.yml` mounts `redis_data:/data` and Redis command includes `--appendonly yes`

---

## Memory Contract Design

### Three Required Operations (exact contract match)

```
PUT  /memory/{userId}/sessions/{sessionId}      → stores session summary
GET  /memory/{userId}/context?relevantTo=signal  → returns relevant sessions + pattern IDs
GET  /memory/{userId}/sessions/{sessionId}       → raw session record for audit
```

**Key Storage Design**:
- Sessions stored at key `memory:{userId}:sessions:{sessionId}` — O(1) retrieval
- Session index per user: `memory:{userId}:session_index` (Redis Set) — O(1) membership check
- Pattern index: `memory:{userId}:patterns:{tag}` — fast tag-based queries
- All data stored as JSON — exact byte-for-byte retrieval for hallucination audit

**Anti-hallucination**: `GET /memory/{userId}/sessions/{sessionId}` returns **exactly** what was stored. The audit endpoint (`POST /audit`) scans actual Redis keys to verify session existence — no LLM involved in this check.

---

## Behavioral Signal Detection: 5 Signals

From Track 1, the 5 signals we detect:
1. `FOMO_BUYING` — momentum chasing without confirmation
2. `REVENGE_TRADING` — emotional recovery trading, often with doubled size
3. `OVERTRADING` — excessive trade count in short windows
4. `PANIC_EXIT` — premature exit due to fear, not plan
5. `OVERCONFIDENCE_SPIRAL` — risk escalation after wins

**Detection method**: Claude prompt with trade data + recent session history. Single-token response to minimize latency.

---

## Latency: ≤ 3s p99 on Warm Calls

**Design choices**:
1. Signal detection is a **separate, short call** (max_tokens=20) to get classification fast
2. Streaming starts **immediately** after signal detection — user sees first token < 400ms
3. Redis queries are O(1) with no blocking
4. Connection pooling via `httpx.AsyncClient` with keep-alive
5. `--workers 2` in uvicorn for request concurrency

**Cold start**: Not measured (per spec: "Cold-start timing does not count")

---

## Streaming: SSE (Server-Sent Events)

**Decision**: SSE over WebSocket

**Rationale**:
- SSE is simpler (no upgrade handshake), works through standard HTTP proxies
- FastAPI's `StreamingResponse` with `text/event-stream` media type
- Token-by-token streaming begins within 400ms of request
- Each SSE event is a JSON object: `{type: "token", text: "..."}` or `{type: "done", full_message: "..."}`
- Metadata event sent first: `{type: "metadata", signal: "FOMO_BUYING", latency_ms: 210}`

---

## Behavioral Profiling: Evidence-Based Claims Only

**Requirement**: Each claim (weakness pattern, failure mode, peak window) must cite specific `sessionId` and `tradeId`.

**Implementation**:
- Prompt explicitly instructs Claude: "Each weakness_pattern MUST cite the specific sessionId and tradeId as evidence. Generic statements without evidence are not acceptable."
- Seed dataset preserves original session/trade IDs so citations are always traceable
- Profile endpoint falls back to seed-data ground truth if AI profile parsing fails

---

## Evaluation Harness: Reproducible from Scratch

**The eval script** (`eval/run_eval.py`) does:
1. Health check (fails fast if service not running)
2. Ingest all 10 seed profiles into memory via `/profile/ingest-seed`
3. Call `/profile/{userId}` for each of 10 traders
4. Compare predicted `pathology_labels` to ground truth labels (fuzzy case-insensitive match)
5. Compute precision, recall, F1 per pathology class + macro averages
6. Run hallucination audit test
7. Verify all 3 memory contract endpoints
8. Save JSON report

**Reproducibility**: Dataset is fixed (`nevup_seed_dataset.json`). Script is deterministic given the same dataset. Reviewers run: `python eval/run_eval.py`

---

## Anti-Hallucination Architecture

Three layers of protection:

1. **Audit Endpoint** (`POST /audit`): Accepts coaching response + session IDs → checks Redis for each ID → returns `found | not-found`. LLM-free, deterministic.

2. **Prompt Engineering**: Coaching prompt explicitly lists only the session IDs that actually exist in context: "When referencing past sessions, ALWAYS cite them by their exact session ID: {listed_ids}. Do not hallucinate session IDs that aren't listed above."

3. **Context Retrieval**: Only sessions that actually exist in Redis are passed to the coaching prompt. The LLM cannot reference sessions it wasn't given.

---

## Why Not LangChain / LlamaIndex?

These frameworks add abstraction layers that make the memory system harder to audit. The requirement is **verifiable memory** — we need to know exactly what's in memory and be able to prove it. Direct Redis + direct Anthropic API calls give us full transparency.

---

## Trade-offs Considered

| Decision | Alternative | Why we chose this |
|----------|-------------|-------------------|
| Redis AOF | PostgreSQL | Simpler, faster, sufficient for session data |
| SSE streaming | WebSocket | Simpler HTTP, no upgrade needed |
| Direct Claude API | LangChain | Full control, auditability |
| Named Docker volume | Bind mount | More portable, Docker-managed lifecycle |
| FastAPI | Flask/Django | Native async, type safety, OpenAPI docs |
