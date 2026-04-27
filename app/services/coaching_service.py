"""
AI Coaching Service - Uses Claude to generate contextual coaching messages.
Detects 9 behavioral pathologies from the NevUp seed dataset schema.
"""

import json
import logging
import os
from typing import AsyncGenerator
import httpx

logger = logging.getLogger(__name__)

ANTHROPIC_API_KEY = os.getenv("ANTHROPIC_API_KEY", "")
ANTHROPIC_URL = "https://api.anthropic.com/v1/messages"
MODEL = "claude-sonnet-4-20250514"

# 9 behavioral pathology signals from the NevUp seed dataset
BEHAVIORAL_SIGNALS = {
    "revenge_trading": "Trader opens new trades within seconds/minutes of a loss in an anxious or fearful state, attempting to immediately recover losses.",
    "overtrading": "Trader places an excessive number of trades in a session, far beyond their historical baseline, often in neutral/greedy states.",
    "fomo_entries": "Trader enters trades driven by fear of missing a move rather than a structured plan; entryRationale is impulsive.",
    "plan_non_adherence": "Trader consistently shows low planAdherence scores (1-2), ignoring their pre-session plan.",
    "premature_exit": "Trader closes winning positions too early, cutting profits short before the trade has reached its target.",
    "loss_running": "Trader holds losing positions well past their stop, letting losses grow instead of cutting them.",
    "session_tilt": "Trader's performance and emotional state deteriorate significantly as a session progresses; later trades are worse than earlier ones.",
    "time_of_day_bias": "Trader performs significantly differently at specific times of day (e.g., wins in morning, loses in afternoon).",
    "position_sizing_inconsistency": "Trader's position sizes (quantity) vary dramatically with no clear logic relative to setup quality.",
}


class CoachingService:

    async def detect_signal(self, trade: dict, session_trades: list[dict]) -> str:
        """Detect which behavioral signal is most present given a trade and session history."""
        prompt = f"""You are a trading psychology expert analyzing a live trade for behavioral pathologies.

Current trade:
{json.dumps(trade, indent=2)}

Previous trades this session:
{json.dumps(session_trades[-6:] if session_trades else [], indent=2)}

Available signals to detect:
{json.dumps(BEHAVIORAL_SIGNALS, indent=2)}

Instructions:
- Check revengeFlag field; if True, lean toward revenge_trading.
- Check planAdherence; if 1-2, lean toward plan_non_adherence.
- Check emotionalState: anxious/fearful after losses → revenge_trading or session_tilt.
- If this trade count is unusually high for the session → overtrading.

Respond with ONLY the signal key (e.g., revenge_trading). If none apply: NONE"""

        response = await self._call_claude(prompt, max_tokens=25)
        signal = response.strip().lower().replace(" ", "_")
        if signal not in BEHAVIORAL_SIGNALS and signal != "none":
            signal = "none"
        return signal

    async def generate_coaching_message(
        self, user_id: str, signal: str, trade: dict, context_sessions: list[dict]
    ) -> str:
        """Generate a non-streaming coaching message."""
        prompt = self._build_coaching_prompt(user_id, signal, trade, context_sessions)
        return await self._call_claude(prompt, max_tokens=400)

    async def stream_coaching_message(
        self, user_id: str, signal: str, trade: dict, context_sessions: list[dict]
    ) -> AsyncGenerator[str, None]:
        """Stream coaching message token by token via SSE."""
        prompt = self._build_coaching_prompt(user_id, signal, trade, context_sessions)
        async for chunk in self._stream_claude(prompt):
            yield chunk

    def _build_coaching_prompt(
        self, user_id: str, signal: str, trade: dict, context_sessions: list[dict]
    ) -> str:
        signal_desc = BEHAVIORAL_SIGNALS.get(signal, "Unknown behavioral pattern")

        context_text = ""
        session_ids_available = []
        if context_sessions:
            for s in context_sessions[:3]:
                sid = s.get("sessionId", "")
                session_ids_available.append(sid)
                context_text += (
                    f"\n- Session {sid[:8]}... ({s.get('timestamp','')[:10]}): "
                    f"{s.get('summary', '')} [Tags: {', '.join(s.get('tags', []))}]"
                )

        ref_instruction = ""
        if session_ids_available:
            full_ids = ", ".join(session_ids_available)
            ref_instruction = (
                f"\nOnly reference sessions by their exact IDs: {full_ids}. "
                "Do NOT invent or guess session IDs."
            )

        return f"""You are a trading psychology coach. Trader {user_id[:8]} just closed a trade.

DETECTED PATTERN: {signal}
Pattern: {signal_desc}

Trade just closed:
- Asset: {trade.get('asset')} ({trade.get('assetClass')}) {trade.get('direction')}
- PnL: ${trade.get('pnl', 0):.2f} | Outcome: {trade.get('outcome', 'unknown')}
- Emotional state: {trade.get('emotionalState', 'unknown')}
- Plan adherence: {trade.get('planAdherence', 'N/A')}/5
- Revenge flag: {trade.get('revengeFlag', False)}
- Rationale: {trade.get('entryRationale', 'N/A')}

Relevant past sessions:{context_text if context_text else " (none found)"}
{ref_instruction}

Write a 2-3 sentence coaching message that:
1. Names the specific {signal} pattern observed
2. References a past session by its exact ID if available
3. Gives one concrete, actionable recommendation

Be direct, empathetic, evidence-based."""

    async def generate_behavioral_profile(self, trader_data: dict) -> dict:
        """Generate a structured behavioral profile from trader session history."""

        # Build a compact summary to avoid huge context
        sessions_summary = []
        for s in trader_data.get("sessions", []):
            trades_summary = []
            for t in s.get("trades", []):
                trades_summary.append({
                    "tradeId": t.get("tradeId"),
                    "asset": t.get("asset"),
                    "outcome": t.get("outcome"),
                    "pnl": t.get("pnl"),
                    "planAdherence": t.get("planAdherence"),
                    "emotionalState": t.get("emotionalState"),
                    "revengeFlag": t.get("revengeFlag"),
                    "entryRationale": t.get("entryRationale"),
                    "entryAt": t.get("entryAt"),
                    "exitAt": t.get("exitAt"),
                    "quantity": t.get("quantity"),
                })
            sessions_summary.append({
                "sessionId": s.get("sessionId"),
                "date": s.get("date"),
                "winRate": s.get("winRate"),
                "totalPnl": s.get("totalPnl"),
                "tradeCount": s.get("tradeCount"),
                "trades": trades_summary,
            })

        prompt = f"""You are a trading psychology expert. Analyze this trader's complete history.

Trader: {trader_data.get('name')} (ID: {trader_data.get('userId')})
Description: {trader_data.get('description', '')}

Sessions:
{json.dumps(sessions_summary, indent=2)}

Available pathology labels:
{json.dumps(list(BEHAVIORAL_SIGNALS.keys()), indent=2)}

Respond ONLY with a JSON object (no markdown, no preamble):
{{
  "pathology_labels": ["list of matching labels from the available set"],
  "weakness_patterns": [
    {{
      "pattern": "label_name",
      "sessionId": "exact-session-uuid-from-data",
      "tradeId": "exact-trade-uuid-from-data",
      "evidence": "specific observable fact from the trade data"
    }}
  ],
  "failure_mode": "one sentence with specific session/trade evidence",
  "peak_window": "sessionId of best-performing session",
  "coaching_priority": "single most important thing to address"
}}

CRITICAL: Only use sessionIds and tradeIds that appear verbatim in the data above."""

        response = await self._call_claude(prompt, max_tokens=700)
        try:
            return json.loads(response)
        except json.JSONDecodeError:
            import re
            match = re.search(r'\{.*\}', response, re.DOTALL)
            if match:
                try:
                    return json.loads(match.group())
                except Exception:
                    pass
            return {"error": "Failed to parse profile", "raw": response}

    async def _call_claude(self, prompt: str, max_tokens: int = 400) -> str:
        headers = {
            "Content-Type": "application/json",
            "x-api-key": ANTHROPIC_API_KEY,
            "anthropic-version": "2023-06-01",
        }
        payload = {
            "model": MODEL,
            "max_tokens": max_tokens,
            "messages": [{"role": "user", "content": prompt}],
        }
        async with httpx.AsyncClient(timeout=30.0) as client:
            response = await client.post(ANTHROPIC_URL, headers=headers, json=payload)
            response.raise_for_status()
            data = response.json()
            return data["content"][0]["text"]

    async def _stream_claude(self, prompt: str) -> AsyncGenerator[str, None]:
        headers = {
            "Content-Type": "application/json",
            "x-api-key": ANTHROPIC_API_KEY,
            "anthropic-version": "2023-06-01",
        }
        payload = {
            "model": MODEL,
            "max_tokens": 400,
            "stream": True,
            "messages": [{"role": "user", "content": prompt}],
        }
        async with httpx.AsyncClient(timeout=60.0) as client:
            async with client.stream("POST", ANTHROPIC_URL, headers=headers, json=payload) as response:
                response.raise_for_status()
                async for line in response.aiter_lines():
                    if line.startswith("data: "):
                        data_str = line[6:]
                        if data_str == "[DONE]":
                            break
                        try:
                            data = json.loads(data_str)
                            if data.get("type") == "content_block_delta":
                                delta = data.get("delta", {})
                                if delta.get("type") == "text_delta":
                                    yield delta.get("text", "")
                        except json.JSONDecodeError:
                            continue
