"""Feature engineering for the dispute model (Part 2).

Every feature is known when the invoice goes out: the shipment record, what was
billed, and what the contract says it should have cost (from the Part 1 audit).
No feature uses dispute_raised or anything from after billing.
"""
import numpy as np
import pandas as pd

from src.audit import TOL
from src.contracts import MERCHANTS

MERCHANT_IDS = sorted(MERCHANTS)   # fixed list, so train and September get the same columns


def billing_error_features(audit: pd.DataFrame) -> pd.DataFrame:
    """Was the merchant overcharged or undercharged vs their contract, and by how much?"""
    net = audit["exp_total"] - audit["billed_total"]          # + = undercharged, - = overcharged
    return pd.DataFrame({
        "is_overbilled": (net < -TOL).astype(int),
        "log_overcharge_inr": np.log1p((-net).clip(lower=0)),
        "log_undercharge_inr": np.log1p(net.clip(lower=0)),
    }, index=audit.index)


def weight_features(audit: pd.DataFrame) -> pd.DataFrame:
    """How far the billed weight is from what the merchant declared (what they see on the invoice)."""
    return pd.DataFrame({
        "log_billed_over_declared": np.log(audit["billed_chargeable_kg"] / audit["declared_wt_kg"]),
        "billed_minus_declared_kg": audit["billed_chargeable_kg"] - audit["declared_wt_kg"],
        "log_vol_over_dead": np.log(audit["exp_vol_kg"] / audit["dead_wt_kg"]),
        "log_billed_kg": np.log1p(audit["billed_chargeable_kg"]),
    }, index=audit.index)


def shipment_features(audit: pd.DataFrame) -> pd.DataFrame:
    """Invoice size and payment mode."""
    return pd.DataFrame({
        "is_cod": (audit["payment_mode"] == "COD").astype(int),
        "log_billed_total": np.log1p(audit["billed_total"]),
    }, index=audit.index)


def merchant_features(audit: pd.DataFrame) -> pd.DataFrame:
    """One column per merchant: each merchant has its own baseline dispute habit."""
    return pd.DataFrame({f"merchant_{m}": (audit["merchant_id"] == m).astype(int) for m in MERCHANT_IDS},
                        index=audit.index)


def build_features(audit: pd.DataFrame) -> pd.DataFrame:
    """Model feature matrix for audit rows (output of src.audit.build_audit)."""
    return pd.concat([
        billing_error_features(audit),
        weight_features(audit),
        shipment_features(audit),
        merchant_features(audit),
    ], axis=1)
