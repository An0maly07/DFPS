"""Assemble the labelled daily training set for the risk model.

Usage: python -m backend.scripts.build_training_set [--start 2010-01-01] [--end YYYY-MM-DD]
Writes backend/data/training/<region>_risk_features.csv and <region>_thresholds.json
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import date, timedelta

from backend.config import DATA_DIR
from backend.models.features import FEATURE_COLUMNS, LABEL_COLUMN, REGION_POINTS, build_dataset

TRAIN_DIR = DATA_DIR / "training"


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--region", default="kolhapur-sangli")
    ap.add_argument("--start", default="2010-01-01")
    # ERA5 archive lags real time by ~5 days
    ap.add_argument("--end", default=(date.today() - timedelta(days=7)).isoformat())
    args = ap.parse_args()

    points = REGION_POINTS[args.region]
    df, rp = build_dataset(points, date.fromisoformat(args.start), date.fromisoformat(args.end))

    TRAIN_DIR.mkdir(parents=True, exist_ok=True)
    csv = TRAIN_DIR / f"{args.region}_risk_features.csv"
    df.to_csv(csv)
    (TRAIN_DIR / f"{args.region}_thresholds.json").write_text(json.dumps(rp, indent=2))

    labelled = df[LABEL_COLUMN].dropna()
    print(f"rows: {len(df)}  labelled: {len(labelled)}  positives: {int(labelled.sum())} "
          f"({100 * labelled.mean():.1f}%)")
    print("return-period flows (m3/s):", rp)
    print("positives per year:")
    print(df[LABEL_COLUMN].groupby(df.index.year).sum().astype(int).to_string())
    print("missing values per feature:")
    print(df[FEATURE_COLUMNS].isna().sum().to_string())
    print("->", csv)
    return 0


if __name__ == "__main__":
    sys.exit(main())
