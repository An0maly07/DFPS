"""XGBoost flood-risk model + SHAP explanations (PLAN.md §3.4).

*** LABELS ARE A PROXY, NOT OBSERVED FLOODS ***
No public daily ground truth of flooding exists for the district, so the target is
rule-derived: `flood_next3d = 1` when GloFAS reanalysis discharge at the Kolhapur
point exceeds the 2-year return-period flow within the next 3 days (see
features.add_label / return_period_flows). The model therefore learns
"observed rain / soil / discharge state today -> high-flow conditions in the next
3 days". It never sees future discharge as a feature, so it is not tautological.
The SAR flood extents (flood_extents table) and the documented 2019 / 2021 events
are used only to *validate* the model, never to train it. Every prediction carries
`label_source = "proxy_glofas_q2yr"` so the API and the pitch can say so.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from functools import lru_cache
from typing import Any

import numpy as np
import pandas as pd
import shap
from sklearn.metrics import average_precision_score, roc_auc_score
from xgboost import XGBClassifier

from backend.config import BACKEND_DIR
from backend.models.features import FEATURE_COLUMNS, LABEL_COLUMN, LEAD_DAYS

ARTIFACT_DIR = BACKEND_DIR / "models" / "artifacts"
MODEL_PATH = ARTIFACT_DIR / "risk_xgb.json"
META_PATH = ARTIFACT_DIR / "risk_meta.json"

LABEL_SOURCE = "proxy_glofas_q2yr"
MODEL_VERSION = "risk-xgb-v1"

# Fixed probability -> alert mapping (PLAN.md: thresholds, not a second model).
ALERT_THRESHOLDS: list[tuple[str, float]] = [
    ("green", 0.0),
    ("yellow", 0.15),
    ("orange", 0.40),
    ("red", 0.70),
]


def alert_level(p: float) -> str:
    level = "green"
    for name, t in ALERT_THRESHOLDS:
        if p >= t:
            level = name
    return level


# --- training ---------------------------------------------------------------------------

def _lead_time_eval(test: pd.DataFrame, proba: np.ndarray) -> dict[int, dict[str, Any]]:
    """For each test year: when did the model first go >= orange before the monsoon
    discharge peak? This is the number behind the PLAN.md §9 narrative."""
    out: dict[int, dict[str, Any]] = {}
    df = test.assign(p=proba)
    orange = dict(ALERT_THRESHOLDS)["orange"]
    for year, g in df.groupby(df.index.year):
        monsoon = g[(g.index.month >= 6) & (g.index.month <= 10)]
        if monsoon.empty:
            continue
        peak_day = monsoon["discharge"].idxmax()
        window = monsoon[(monsoon.index >= peak_day - pd.Timedelta(days=30)) & (monsoon.index <= peak_day)]
        flagged = window[window["p"] >= orange]
        first = flagged.index[0] if not flagged.empty else None
        out[int(year)] = {
            "peak_date": peak_day.date().isoformat(),
            "peak_discharge": round(float(monsoon.loc[peak_day, "discharge"]), 1),
            "first_orange_date": first.date().isoformat() if first is not None else None,
            "lead_days": int((peak_day - first).days) if first is not None else None,
            "max_level_in_window": alert_level(float(window["p"].max())),
            "max_p_in_window": round(float(window["p"].max()), 3),
            # 0 here means the proxy label never fired that year even though the
            # documented flood happened — i.e. the model's alert came from the
            # rainfall/soil signal, not from having seen a positive label.
            "proxy_label_positives": int(g[LABEL_COLUMN].sum()),
        }
    return out


def train(
    df: pd.DataFrame,
    q_flood: float,
    test_years: tuple[int, ...] = (2019, 2021),
    random_state: int = 42,
) -> dict[str, Any]:
    """Train on all labelled days except `test_years`, evaluate on those, save artifacts."""
    labelled = df.dropna(subset=[LABEL_COLUMN])
    is_test = labelled.index.year.isin(test_years)
    X_tr, y_tr = labelled.loc[~is_test, FEATURE_COLUMNS], labelled.loc[~is_test, LABEL_COLUMN].astype(int)
    X_te, y_te = labelled.loc[is_test, FEATURE_COLUMNS], labelled.loc[is_test, LABEL_COLUMN].astype(int)

    pos, neg = int(y_tr.sum()), int((y_tr == 0).sum())
    model = XGBClassifier(
        n_estimators=400,
        max_depth=4,
        learning_rate=0.05,
        subsample=0.8,
        colsample_bytree=0.8,
        min_child_weight=5,
        reg_lambda=1.0,
        scale_pos_weight=neg / max(pos, 1),
        eval_metric="aucpr",
        n_jobs=4,
        random_state=random_state,
    )
    model.fit(X_tr, y_tr, eval_set=[(X_te, y_te)], verbose=False)

    p_tr = model.predict_proba(X_tr)[:, 1]
    p_te = model.predict_proba(X_te)[:, 1]
    metrics = {
        "train": {"n": int(len(y_tr)), "positives": pos, "roc_auc": round(float(roc_auc_score(y_tr, p_tr)), 4),
                  "pr_auc": round(float(average_precision_score(y_tr, p_tr)), 4)},
        "test": {"years": list(test_years), "n": int(len(y_te)), "positives": int(y_te.sum()),
                 "roc_auc": round(float(roc_auc_score(y_te, p_te)), 4),
                 "pr_auc": round(float(average_precision_score(y_te, p_te)), 4)},
        "lead_time": _lead_time_eval(labelled.loc[is_test], p_te),
        "feature_importance_gain": {
            k: round(float(v), 4)
            for k, v in sorted(model.get_booster().get_score(importance_type="gain").items(), key=lambda kv: -kv[1])
        },
    }

    ARTIFACT_DIR.mkdir(parents=True, exist_ok=True)
    model.save_model(MODEL_PATH)
    meta = {
        "model_version": MODEL_VERSION,
        "label_source": LABEL_SOURCE,
        "label_definition": (
            f"1 if max GloFAS reanalysis discharge over the next {LEAD_DAYS} days >= q_2yr "
            f"({q_flood:.1f} m3/s, 2-year return period from annual maxima)"
        ),
        "q_flood": q_flood,
        "features": FEATURE_COLUMNS,
        "alert_thresholds": ALERT_THRESHOLDS,
        "trained_at": datetime.now(timezone.utc).isoformat(),
        "train_period": [labelled.index.min().date().isoformat(), labelled.index.max().date().isoformat()],
        "metrics": metrics,
    }
    META_PATH.write_text(json.dumps(meta, indent=2))
    load_model.cache_clear()
    return meta


# --- inference ----------------------------------------------------------------------------

class RiskModel:
    def __init__(self, model_path=MODEL_PATH, meta_path=META_PATH):
        if not model_path.exists():
            raise FileNotFoundError(f"{model_path} missing — run backend.scripts.train_risk_model first")
        self.model = XGBClassifier()
        self.model.load_model(model_path)
        self.meta = json.loads(meta_path.read_text())
        self.explainer = shap.TreeExplainer(self.model)

    def predict(self, features: dict[str, float]) -> dict[str, Any]:
        X = pd.DataFrame([features]).reindex(columns=FEATURE_COLUMNS).astype(float)
        p = float(self.model.predict_proba(X)[0, 1])
        sv = np.asarray(self.explainer.shap_values(X))[0]
        base = self.explainer.expected_value
        base = float(np.ravel(base)[-1]) if np.ndim(base) else float(base)
        return {
            "risk_score": round(p, 4),
            "alert_level": alert_level(p),
            "shap_values": {f: round(float(v), 4) for f, v in zip(FEATURE_COLUMNS, sv)},
            "shap_base_value": round(base, 4),
            "shap_units": "log-odds",
            "features": {f: X.iloc[0][f] for f in FEATURE_COLUMNS},
            "label_source": self.meta["label_source"],
            "model_version": self.meta["model_version"],
        }


@lru_cache(maxsize=1)
def load_model() -> RiskModel:
    return RiskModel()
