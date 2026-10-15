"""Dispute-model features: no leakage, stable columns, sensible values."""
import numpy as np
import pandas as pd

from src.audit import build_audit
from src.features import MERCHANT_IDS, build_features


def _audit(**kw):
    base = dict(awb="T1", ship_date=pd.Timestamp("2026-09-10"), merchant_id="M09",
                origin_city="Ahmedabad", dest_city="Delhi", dest_state="Delhi",
                declared_wt_kg=0.4, dead_wt_kg=0.4, length_cm=10, width_cm=10, height_cm=10,
                payment_mode="Prepaid", cod_value=0, billed_zone="D", billed_chargeable_kg=0.5,
                billed_freight=47.84, billed_cod_fee=0.0, billed_total=47.84,
                dispute_raised=np.nan)
    base.update(kw)
    return build_audit(pd.DataFrame([base]))


def test_target_not_in_features():
    X = build_features(_audit())
    assert not any("dispute" in c for c in X.columns)


def test_same_columns_for_any_merchant():
    a = build_features(_audit())
    b = build_features(_audit(merchant_id="M01", origin_city="Mumbai"))
    assert list(a.columns) == list(b.columns)
    assert [c for c in a.columns if c.startswith("merchant_")] == [f"merchant_{m}" for m in MERCHANT_IDS]


def test_overbilled_flag_and_amount():
    # M09 in Sep should be 52 * 0.88 = 45.76; billed 47.84 at the old 8% -> overcharged 2.08
    X = build_features(_audit()).iloc[0]
    assert X["is_overbilled"] == 1
    assert np.isclose(X["log_overcharge_inr"], np.log1p(2.08))
    assert X["log_undercharge_inr"] == 0


def test_features_finite():
    X = build_features(_audit())
    assert np.isfinite(X.to_numpy(dtype=float)).all()
