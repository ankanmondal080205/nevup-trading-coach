#!/usr/bin/env python3
"""
NevUp Hackathon 2026 - Track 2 Evaluation Script
Run against the 10 real seed trader profiles.

Usage:
    python eval/run_eval.py [--api-url http://localhost:8000] [--output eval_report.json]

Requirements:
    pip install httpx
"""

import argparse, asyncio, json, sys
from datetime import datetime
from pathlib import Path

try:
    import httpx
except ImportError:
    print("ERROR: pip install httpx"); sys.exit(1)

SEED_PATH = Path(__file__).parent.parent / "nevup_seed_dataset.json"

ALL_PATHOLOGIES = [
    "revenge_trading", "overtrading", "fomo_entries", "plan_non_adherence",
    "premature_exit", "loss_running", "session_tilt", "time_of_day_bias",
    "position_sizing_inconsistency",
]


def load_seed():
    if not SEED_PATH.exists():
        print(f"ERROR: Seed dataset not found at {SEED_PATH}"); sys.exit(1)
    with open(SEED_PATH) as f:
        return json.load(f)


def sep(char="-", n=60): print(char * n)


def labels_match(a, b):
    return a.lower().replace("_","") == b.lower().replace("_","")


async def run(api_url: str, output: str):
    sep("=")
    print("NevUp 2026 — Track 2 Evaluation Harness")
    print(f"API : {api_url}   Time: {datetime.utcnow().isoformat()[:19]}")
    sep("="); print()

    seed = load_seed()
    print(f"✓ Dataset: {seed['meta']['traderCount']} traders | "
          f"{seed['meta']['totalSessions']} sessions | "
          f"{seed['meta']['totalTrades']} trades\n")

    async with httpx.AsyncClient(base_url=api_url, timeout=90.0) as c:

        # ── 1. Health ──────────────────────────────────────────────────
        print("1. Health check")
        try:
            r = await c.get("/health"); r.raise_for_status()
            print(f"   ✓ {r.json()}\n")
        except Exception as e:
            print(f"   ✗ FAILED: {e}\n   Run: docker compose up"); sys.exit(1)

        # ── 2. Ingest seed ─────────────────────────────────────────────
        print("2. Ingest seed dataset")
        try:
            r = await c.post("/profile/ingest-seed"); r.raise_for_status()
            d = r.json()
            print(f"   ✓ {d['ingested_sessions']} sessions ingested for {d['traders']} traders\n")
        except Exception as e:
            print(f"   ✗ Ingest failed: {e}\n")

        # ── 3. Profile classification ──────────────────────────────────
        print("3. Behavioral profile classification")
        sep()
        ground_truth, predictions, per_trader = {}, {}, []

        for trader in seed["traders"]:
            uid   = trader["userId"]
            name  = trader["name"]
            gt    = set(trader.get("groundTruthPathologies", []))
            ground_truth[uid] = gt

            try:
                r = await c.get(f"/profile/{uid}"); r.raise_for_status()
                prof = r.json()
                pred = set(prof.get("pathology_labels", []))
            except Exception as e:
                print(f"   ✗ {name}: {e}"); pred = set()

            predictions[uid] = pred
            matched = {g for g in gt if any(labels_match(g, p) for p in pred)}
            ok = "✓" if matched or (not gt and not pred) else "✗"
            print(f"   {ok} {name}")
            print(f"      GT:      {sorted(gt) or '[]'}")
            print(f"      Pred:    {sorted(pred) or '[]'}")
            print(f"      Matched: {sorted(matched) or 'NONE'}")
            per_trader.append({
                "userId": uid, "name": name,
                "ground_truth": sorted(gt), "predicted": sorted(pred),
                "matched": sorted(matched),
            })
        print()

        # ── 4. Per-class metrics ───────────────────────────────────────
        print("4. Per-class metrics")
        sep()
        class_metrics = {}
        header = f"{'Pathology':<38} {'P':>6} {'R':>6} {'F1':>6}  TP FP FN"
        print(f"   {header}")
        print(f"   {'-'*len(header)}")

        for path in ALL_PATHOLOGIES:
            tp = sum(1 for uid in ground_truth
                     if any(labels_match(path,g) for g in ground_truth[uid])
                     and any(labels_match(path,p) for p in predictions.get(uid,set())))
            fp = sum(1 for uid in predictions
                     if not any(labels_match(path,g) for g in ground_truth.get(uid,set()))
                     and any(labels_match(path,p) for p in predictions[uid]))
            fn = sum(1 for uid in ground_truth
                     if any(labels_match(path,g) for g in ground_truth[uid])
                     and not any(labels_match(path,p) for p in predictions.get(uid,set())))
            pr = tp/(tp+fp) if (tp+fp) else 0.0
            rc = tp/(tp+fn) if (tp+fn) else 0.0
            f1 = 2*pr*rc/(pr+rc) if (pr+rc) else 0.0
            class_metrics[path] = {"precision":round(pr,3),"recall":round(rc,3),"f1":round(f1,3),"tp":tp,"fp":fp,"fn":fn}
            print(f"   {path:<38} {pr:>6.3f} {rc:>6.3f} {f1:>6.3f}  {tp:2} {fp:2} {fn:2}")

        mp  = sum(m["precision"] for m in class_metrics.values())/len(class_metrics)
        mr  = sum(m["recall"]    for m in class_metrics.values())/len(class_metrics)
        mf1 = sum(m["f1"]        for m in class_metrics.values())/len(class_metrics)
        sep()
        print(f"   {'MACRO':<38} {mp:>6.3f} {mr:>6.3f} {mf1:>6.3f}\n")

        # ── 5. Hallucination audit ─────────────────────────────────────
        print("5. Hallucination audit")
        sep()
        # Use real session IDs from the dataset
        real_sids = [seed["traders"][0]["sessions"][0]["sessionId"],
                     seed["traders"][1]["sessions"][0]["sessionId"]]
        fake_sid  = "00000000-0000-0000-0000-000000000000"
        audit_payload = {
            "coaching_response": (
                f"Based on session {real_sids[0]} you showed revenge_trading. "
                f"In {real_sids[1]} overtrading was detected. "
                f"Session {fake_sid} also showed issues."
            ),
            "referenced_session_ids": real_sids + [fake_sid],
        }
        audit_result = {}
        try:
            r = await c.post("/audit", json=audit_payload); r.raise_for_status()
            audit_result = r.json()
            print(f"   ✓ {audit_result['found_count']}/{audit_result['total_referenced']} sessions found")
            print(f"   Hallucination rate: {audit_result['hallucination_rate']:.0%}")
            for res in audit_result["audit_results"]:
                flag = "✓ FOUND" if res["found"] else "✗ NOT FOUND (hallucination)"
                print(f"   {res['sessionId'][:20]}...  {flag}")
        except Exception as e:
            print(f"   ✗ Audit failed: {e}")
        print()

        # ── 6. Memory contract ─────────────────────────────────────────
        print("6. Memory contract verification")
        sep()
        tu, ts = "eval_test_user", "eval_test_session"
        try:
            r = await c.put(f"/memory/{tu}/sessions/{ts}", json={
                "summary": "Eval test session",
                "metrics": {"winRate":0.6,"avgPnl":150.0,"maxDrawdown":-200.0,"tradeCount":5,"avgDuration_min":45.0},
                "tags": ["revenge_trading"]
            }); r.raise_for_status()
            print(f"   ✓ PUT /memory/{tu}/sessions/{ts}")
        except Exception as e: print(f"   ✗ PUT: {e}")

        try:
            r = await c.get(f"/memory/{tu}/context?relevantTo=revenge_trading"); r.raise_for_status()
            ctx = r.json()
            print(f"   ✓ GET context — {len(ctx['sessions'])} sessions, patterns: {ctx['patternIds']}")
        except Exception as e: print(f"   ✗ GET context: {e}")

        try:
            r = await c.get(f"/memory/{tu}/sessions/{ts}"); r.raise_for_status()
            print(f"   ✓ GET session — raw record returned")
        except Exception as e: print(f"   ✗ GET session: {e}")
        print()

        # ── Save report ────────────────────────────────────────────────
        report = {
            "metadata": {
                "hackathon": "NevUp 2026", "track": "Track 2",
                "generated_at": datetime.utcnow().isoformat(),
                "api_url": api_url, "dataset": "nevup_seed_dataset.json",
            },
            "summary": {"macro_precision": round(mp,3), "macro_recall": round(mr,3), "macro_f1": round(mf1,3)},
            "per_class_metrics": class_metrics,
            "per_trader_results": per_trader,
            "audit_test": audit_result,
        }
        with open(output, "w") as f:
            json.dump(report, f, indent=2)

        sep("=")
        print(f"✓ Report saved → {output}")
        print(f"  Macro F1: {mf1:.3f}  |  Precision: {mp:.3f}  |  Recall: {mr:.3f}")
        sep("=")


if __name__ == "__main__":
    p = argparse.ArgumentParser()
    p.add_argument("--api-url", default="http://localhost:8000")
    p.add_argument("--output",  default="eval_report.json")
    args = p.parse_args()
    asyncio.run(run(args.api_url, args.output))
