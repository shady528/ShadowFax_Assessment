"""Dispute model (Part 2): logistic regression on src/features.py features.

Why logistic regression: on ~2,600 rows a month it beat LightGBM on held-out
log loss and AUC, its probabilities are well calibrated, and each coefficient
can be read off and explained to ops.
"""
import numpy as np
import pandas as pd
from sklearn.linear_model import LogisticRegression
from sklearn.metrics import average_precision_score, brier_score_loss, log_loss, roc_auc_score
from sklearn.model_selection import StratifiedKFold
from sklearn.pipeline import make_pipeline
from sklearn.preprocessing import StandardScaler

from src.features import build_features

TARGET = "dispute_raised"
C = 0.1           # regularisation strength (smaller = stronger); chosen on Jul -> Aug log loss
SEED = 0


def make_model(c: float = C):
    """Standardise features, then L2-regularised logistic regression."""
    return make_pipeline(StandardScaler(), LogisticRegression(C=c, max_iter=2000))


def score(y_true, prob) -> dict:
    """Metrics for probability predictions. log loss is the primary one."""
    return {
        "log_loss": log_loss(y_true, prob),
        "brier": brier_score_loss(y_true, prob),
        "roc_auc": roc_auc_score(y_true, prob),
        "pr_auc": average_precision_score(y_true, prob),
        "mean_prob": float(pd.Series(prob).mean()),
        "actual_rate": float(pd.Series(y_true).mean()),
    }


def baseline_score(train: pd.DataFrame, valid: pd.DataFrame) -> dict:
    """Predict the training dispute rate for everyone -- the bar a model has to beat."""
    prob = [train[TARGET].mean()] * len(valid)
    return {"log_loss": log_loss(valid[TARGET], prob), "brier": brier_score_loss(valid[TARGET], prob),
            "roc_auc": 0.5, "pr_auc": valid[TARGET].mean(), "mean_prob": prob[0],
            "actual_rate": valid[TARGET].mean()}


def fit(train: pd.DataFrame, c: float = C):
    return make_model(c).fit(build_features(train), train[TARGET])


def predict(model, rows: pd.DataFrame) -> pd.Series:
    return pd.Series(model.predict_proba(build_features(rows))[:, 1], index=rows.index, name="prob")


def time_split_scores(labelled: pd.DataFrame) -> pd.DataFrame:
    """Train on one month, score the next (and the reverse) -- mirrors Jul+Aug -> Sep."""
    month = labelled["ship_date"].dt.month
    jul, aug = labelled[month == 7], labelled[month == 8]
    rows = {
        "baseline: Jul rate -> Aug": baseline_score(jul, aug),
        "model: train Jul -> score Aug": score(aug[TARGET], predict(fit(jul), aug)),
        "model: train Aug -> score Jul": score(jul[TARGET], predict(fit(aug), jul)),
    }
    return pd.DataFrame(rows).T


def cv_scores(labelled: pd.DataFrame, folds: int = 5) -> pd.DataFrame:
    """Stratified k-fold on Jul+Aug: how stable the score is across random splits."""
    out = []
    for i, (tr, va) in enumerate(StratifiedKFold(folds, shuffle=True, random_state=SEED)
                                 .split(labelled, labelled[TARGET])):
        train, valid = labelled.iloc[tr], labelled.iloc[va]
        out.append({"fold": i + 1, **score(valid[TARGET], predict(fit(train), valid))})
    return pd.DataFrame(out).set_index("fold")


def regularisation_scan(labelled: pd.DataFrame, values=(0.01, 0.03, 0.1, 0.3, 1.0)) -> pd.DataFrame:
    """Jul -> Aug log loss / AUC for a few C values (how C was picked)."""
    month = labelled["ship_date"].dt.month
    jul, aug = labelled[month == 7], labelled[month == 8]
    return pd.DataFrame({c: score(aug[TARGET], predict(fit(jul, c), aug)) for c in values}).T.rename_axis("C")


def calibration_table(y_true: pd.Series, prob: pd.Series, bins: int = 5) -> pd.DataFrame:
    """Mean predicted vs actual dispute rate, by predicted-probability quintile."""
    t = pd.DataFrame({"prob": prob.values, "actual": y_true.values})
    t["bin"] = pd.qcut(t["prob"], bins)
    return t.groupby("bin", observed=True).agg(predicted=("prob", "mean"), actual=("actual", "mean"),
                                               shipments=("actual", "size"))


def coefficients(model) -> pd.Series:
    """Standardised coefficients: + raises dispute odds, - lowers them (per 1 std dev of the feature)."""
    names = model[0].feature_names_in_
    return pd.Series(model[-1].coef_[0], index=names).sort_values()


def expected_log_loss(prob: pd.Series) -> float:
    """Log loss we'd get if these probabilities were perfectly calibrated (mean binary entropy)."""
    p = prob.clip(1e-12, 1 - 1e-12)
    return float(-(p * np.log(p) + (1 - p) * np.log(1 - p)).mean())


# ---------------------------------------------------------------------------
# Q3 helpers: what drives disputes, and what fixing billing would change
# ---------------------------------------------------------------------------

def _rate(labelled: pd.DataFrame, key: pd.Series, name: str) -> pd.DataFrame:
    t = labelled.groupby(key.rename(name), observed=True)[TARGET].agg(shipments="size", dispute_rate="mean")
    return t.round(3)


def driver_tables(labelled: pd.DataFrame) -> dict:
    """Observed Jul+Aug dispute rates for the main drivers (raw data, not model output)."""
    net = labelled["exp_total"] - labelled["billed_total"]
    billing = pd.Series("billed correctly", index=labelled.index)
    billing[net < -0.005] = "overcharged"
    billing[net > 0.005] = "undercharged"
    zone_wrong = labelled["billed_zone"] != labelled["exp_zone"]
    zone_split = pd.Series("zone correct", index=labelled.index)
    zone_split[zone_wrong & (net < -0.005)] = "zone wrong, net overcharged"
    zone_split[zone_wrong & (net > 0.005)] = "zone wrong, net undercharged"
    ratio = labelled["billed_chargeable_kg"] / labelled["declared_wt_kg"]
    correct = billing == "billed correctly"
    return {
        "billing vs contract": _rate(labelled, billing, "billing"),
        "zone error: overcharge vs not": _rate(labelled, zone_split, "zone"),
        "billed / declared weight (correctly billed only)":
            _rate(labelled[correct], pd.qcut(ratio[correct], 5), "billed_over_declared"),
        "payment mode": _rate(labelled, labelled["payment_mode"], "payment_mode"),
        "merchant": _rate(labelled, labelled["merchant_id"], "merchant_id").sort_values("dispute_rate"),
    }


def billed_correctly(rows: pd.DataFrame) -> pd.DataFrame:
    """Same shipments, but as if billing had matched the contract (billed = expected)."""
    fixed = rows.copy()
    fixed["billed_zone"] = fixed["exp_zone"]
    fixed["billed_chargeable_kg"] = fixed["exp_chargeable_kg"]
    fixed["billed_freight"] = fixed["exp_freight"]
    fixed["billed_cod_fee"] = fixed["exp_cod_fee"]
    fixed["billed_total"] = fixed["exp_total"]
    return fixed


def what_if_fixed(model, rows: pd.DataFrame) -> pd.DataFrame:
    """Expected disputes per billing status: as billed vs if billed correctly."""
    as_billed = predict(model, rows)
    fixed = predict(model, billed_correctly(rows))
    status = rows["status"]
    out = pd.DataFrame({"shipments": status.value_counts(),
                        "expected_disputes_as_billed": as_billed.groupby(status).sum(),
                        "expected_disputes_if_fixed": fixed.groupby(status).sum()})
    out.loc["total"] = out.sum()
    out["avoided"] = out["expected_disputes_as_billed"] - out["expected_disputes_if_fixed"]
    return out.round(1)


def write_predictions(rows: pd.DataFrame, prob: pd.Series, path) -> pd.DataFrame:
    """CSV with header awb,prob -- one row per shipment, prob in [0, 1]."""
    out = pd.DataFrame({"awb": rows["awb"].values, "prob": prob.round(6).values})
    assert out["awb"].is_unique and out["prob"].between(0, 1).all()
    out.to_csv(path, index=False)
    return out
