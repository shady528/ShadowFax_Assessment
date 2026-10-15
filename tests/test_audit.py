"""Tests for the audit's reason attribution (amounts don't depend on these labels)."""
import pandas as pd

from src.audit import attribute_impact, build_audit


def _shipment(**kw):
    base = dict(awb="T1", ship_date=pd.Timestamp("2026-07-15"), merchant_id="M01",
                origin_city="Mumbai", dest_city="Delhi", dest_state="Delhi",
                declared_wt_kg=0.4, dead_wt_kg=0.4, length_cm=10, width_cm=10, height_cm=10,
                payment_mode="Prepaid", cod_value=0, billed_zone="C", billed_chargeable_kg=0.5,
                billed_freight=41.40, billed_cod_fee=0.0, billed_total=41.40, dispute_raised=0)
    base.update(kw)
    return pd.DataFrame([base])


def test_correct_bill_is_ok():
    a = build_audit(_shipment()).iloc[0]
    assert a["status"] == "ok" and a["reason"] == "ok" and a["diff_total"] == 0


def test_underbilled_m07_minimum():
    # M07 Hyderabad->Delhi (C), 0.4 kg -> 1.0 kg: 45+24=69*0.9=62.10; billed at 0.5 kg = 40.50
    df = _shipment(merchant_id="M07", origin_city="Hyderabad", billed_chargeable_kg=0.5,
                   billed_freight=40.50, billed_total=40.50)
    a = build_audit(df).iloc[0]
    assert a["status"] == "underbilled"
    assert a["diff_total"] == -21.60
    assert "M07-§2.3" in a["reason"]


def test_overbilled_m09_old_discount():
    # M09 Ahmedabad->Delhi (D), Sep: 52*0.88=45.76 expected; billed at 8% = 47.84
    df = _shipment(merchant_id="M09", origin_city="Ahmedabad", ship_date=pd.Timestamp("2026-09-10"),
                   billed_zone="D", billed_freight=47.84, billed_total=47.84)
    a = build_audit(df).iloc[0]
    assert a["status"] == "overbilled"
    assert a["diff_total"] == 2.08
    assert "8%" in a["reason"]


def test_impact_split_adds_up_to_diff():
    # M07 Hyderabad->Mumbai: billed zone D at 0.5 kg (two errors: zone + M07 minimum)
    df = _shipment(merchant_id="M07", origin_city="Hyderabad", dest_city="Mumbai", dest_state="Maharashtra",
                   billed_zone="D", billed_freight=46.80, billed_total=46.80)
    a = build_audit(df)
    impact = attribute_impact(a)
    assert len(impact) == 2
    # zone fix: D 0.5 kg 46.80 -> C 0.5 kg 40.50 (-6.30); weight fix: C 0.5 -> 1.0 kg 62.10 (+21.60)
    assert sorted(impact["impact_inr"]) == [-6.30, 21.60]
    assert round(impact["impact_inr"].sum(), 2) == -a["diff_total"].iloc[0] == 15.30
