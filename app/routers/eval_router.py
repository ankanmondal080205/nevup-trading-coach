"""
Evaluation Harness Router
Runs classification against the 10 real seed trader profiles.
Produces precision, recall, F1 per pathology class.
"""

import json
import logging
from pathlib import Path
from fastapi import APIRouter, Request, HTTPException
from app.services.coaching_service import CoachingService

router = APIRouter()
logger = logging.getLogger(__name__)
coaching_service = CoachingService()

SEED_PATH = Path("/app/nevup_seed_dataset.json")

ALL_PATHOLOGIES = [
    "revenge_trading", "overtrading", "fomo_entries", "plan_non_adherence",
    "premature_exit", "loss_running", "session_tilt", "time_of_day_bias",
    "position_sizing_inconsistency",
]


def load_seed():
    for p in [SEED_PATH, Path("nevup_seed_dataset.json")]:
        if p.exists():
            with open(p) as f:
                return json.load(f)
    raise FileNotFoundError("Seed dataset not found")


def labels_match(a: str, b: str) -> bool:
    return a.lower().replace("_", "") == b.lower().replace("_", "")


@router.post("/run")
async def run_evaluation(request: Request):
    """
    POST /eval/run
    Classification report: precision, recall, F1 per pathology class.
    Uses the 10 ground-truth trader profiles from nevup_seed_dataset.json.
    """
    try:
        seed = load_seed()
    except FileNotFoundError:
        raise HTTPException(status_code=404, detail="Seed dataset not found")

    ground_truth = {}
    predictions = {}
    per_trader = []

    for trader in seed["traders"]:
        uid = trader["userId"]
        gt = set(trader.get("groundTruthPathologies", []))
        ground_truth[uid] = gt

        try:
            profile = await coaching_service.generate_behavioral_profile(trader)
            pred = set(profile.get("pathology_labels", []))
        except Exception as e:
            logger.error(f"Profile failed for {uid}: {e}")
            pred = set()

        predictions[uid] = pred

        # Compute match
        matched = {g for g in gt if any(labels_match(g, p) for p in pred)}

        per_trader.append({
            "userId": uid,
            "name": trader.get("name"),
            "ground_truth": sorted(gt),
            "predicted": sorted(pred),
            "matched": sorted(matched),
            "failure_mode": trader.get("description", ""),
        })

    # Per-class metrics
    class_metrics = {}
    for pathology in ALL_PATHOLOGIES:
        tp = sum(1 for uid in ground_truth
                 if any(labels_match(pathology, g) for g in ground_truth[uid])
                 and any(labels_match(pathology, p) for p in predictions.get(uid, set())))
        fp = sum(1 for uid in predictions
                 if not any(labels_match(pathology, g) for g in ground_truth.get(uid, set()))
                 and any(labels_match(pathology, p) for p in predictions[uid]))
        fn = sum(1 for uid in ground_truth
                 if any(labels_match(pathology, g) for g in ground_truth[uid])
                 and not any(labels_match(pathology, p) for p in predictions.get(uid, set())))

        precision = tp / (tp + fp) if (tp + fp) > 0 else 0.0
        recall    = tp / (tp + fn) if (tp + fn) > 0 else 0.0
        f1        = 2 * precision * recall / (precision + recall) if (precision + recall) > 0 else 0.0

        class_metrics[pathology] = {
            "precision": round(precision, 3),
            "recall":    round(recall, 3),
            "f1":        round(f1, 3),
            "tp": tp, "fp": fp, "fn": fn,
        }

    macro_p  = sum(m["precision"] for m in class_metrics.values()) / len(class_metrics)
    macro_r  = sum(m["recall"]    for m in class_metrics.values()) / len(class_metrics)
    macro_f1 = sum(m["f1"]        for m in class_metrics.values()) / len(class_metrics)

    return {
        "evaluation_summary": {
            "dataset": "nevup_seed_dataset.json",
            "total_traders": len(seed["traders"]),
            "total_pathology_classes": len(ALL_PATHOLOGIES),
            "macro_precision": round(macro_p, 3),
            "macro_recall":    round(macro_r, 3),
            "macro_f1":        round(macro_f1, 3),
        },
        "per_class_metrics": class_metrics,
        "per_trader_results": per_trader,
    }
