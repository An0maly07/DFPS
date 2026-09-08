"""Train the XGBoost risk model on the assembled dataset and (optionally) score today.

Usage:
  python -m backend.scripts.train_risk_model
  python -m backend.scripts.train_risk_model --score-today [--write]
"""

from __future__ import annotations

import argparse
import json
import sys

import pandas as pd

from backend.config import DATA_DIR
from backend.models import xgboost_risk
from backend.models.features import REGION_POINTS, live_features
from backend.services import supabase_client as db

TRAIN_DIR = DATA_DIR / "training"


def score_today(region: str, write: bool) -> None:
    points = REGION_POINTS[region]
    row, ctx = live_features(points)
    model = xgboost_risk.load_model()
    pred = model.predict(row)
    print(json.dumps({k: v for k, v in pred.items()}, indent=1, default=float))
    print("discharge_forecast_max3d:", ctx["discharge_forecast_max3d"])
    if write:
        region_row = db.get_region(region)
        saved = db.insert_risk_score(
            {
                "region_id": region_row["id"],
                "rainfall_mm": row["rain_mm"],
                "soil_moisture_pct": row["soil_moisture_pct"],
                "temperature_c": row["temperature_c"],
                "discharge_forecast": ctx["discharge_forecast_max3d"],
                "risk_score": pred["risk_score"],
                "alert_level": pred["alert_level"],
                "shap_json": {
                    "values": pred["shap_values"],
                    "base_value": pred["shap_base_value"],
                    "units": pred["shap_units"],
                    "features": pred["features"],
                    "label_source": pred["label_source"],
                    "model_version": pred["model_version"],
                    "scored_for": ctx["scored_for"],
                },
            }
        )
        print("risk_scores row:", saved["id"], saved["scored_at"], saved["alert_level"])


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--region", default="kolhapur-sangli")
    ap.add_argument("--score-today", action="store_true")
    ap.add_argument("--write", action="store_true", help="insert today's score into risk_scores")
    ap.add_argument("--skip-train", action="store_true")
    args = ap.parse_args()

    if not args.skip_train:
        df = pd.read_csv(TRAIN_DIR / f"{args.region}_risk_features.csv", index_col=0, parse_dates=True)
        rp = json.loads((TRAIN_DIR / f"{args.region}_thresholds.json").read_text())
        meta = xgboost_risk.train(df, q_flood=rp["q_2yr"])
        print(json.dumps(meta["metrics"], indent=1))
        print("artifacts ->", xgboost_risk.MODEL_PATH, xgboost_risk.META_PATH)

    if args.score_today:
        score_today(args.region, args.write)
    return 0


if __name__ == "__main__":
    sys.exit(main())
