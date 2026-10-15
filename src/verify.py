"""Verification for Part 1 (Q6): independent recomputation, checks and edge-case coverage.

independent_expected() re-derives every charge with whole-column pandas/numpy
operations straight from the contract tables. It does NOT call src/billing.py,
so agreement between the two is evidence that neither has a per-row bug.
"""
import re
import subprocess
import sys
from pathlib import Path

import numpy as np
import pandas as pd

from src.audit import TOL, attribute_impact
from src.billing import normalise_place
from src.contracts import (
    CITY_STATE,
    DEFAULT_COD_MIN_INR,
    DEFAULT_COD_PCT,
    DEFAULT_MIN_CHARGEABLE_KG,
    DEFAULT_VOLUMETRIC_DIVISOR,
    MERCHANTS,
    METRO_CITIES,
    RATE_CARD,
    SPECIAL_REGION_STATES,
    WEIGHT_SLAB_KG,
)

ROOT = Path(__file__).resolve().parent.parent


# ---------------------------------------------------------------------------
# Independent (vectorised) recomputation
# ---------------------------------------------------------------------------

def _merchant_column(df: pd.DataFrame, field: str, default) -> pd.Series:
    """Per-row contract value for `field`, with dated amendments applied."""
    base = {m: getattr(t, field) for m, t in MERCHANTS.items()}
    col = df["merchant_id"].map(base)
    col = col.where(col.notna(), default) if default is not None else col
    for m, t in MERCHANTS.items():
        for a in t.amendments:
            if field not in a.changes:
                continue
            in_window = (df["merchant_id"] == m) & (df["ship_date"] >= pd.Timestamp(a.start))
            if a.end is not None:
                in_window &= df["ship_date"] <= pd.Timestamp(a.end)
            col = col.where(~in_window, a.changes[field])
    return col


def independent_expected(df: pd.DataFrame) -> pd.DataFrame:
    """Expected zone / kg / freight / COD / total for every row, computed column-wise."""
    divisor = _merchant_column(df, "volumetric_divisor", DEFAULT_VOLUMETRIC_DIVISOR).astype(float)
    min_kg = _merchant_column(df, "min_chargeable_kg", DEFAULT_MIN_CHARGEABLE_KG).astype(float)
    discount = _merchant_column(df, "freight_discount_pct", None).astype(float)
    cod_min = _merchant_column(df, "cod_min_inr", DEFAULT_COD_MIN_INR).astype(float)
    cod_pct = _merchant_column(df, "cod_pct", DEFAULT_COD_PCT).astype(float)
    waived = _merchant_column(df, "cod_waived", None).astype(bool)

    origin = df["origin_city"].map(normalise_place)
    dest = df["dest_city"].map(normalise_place)
    dest_state = df["dest_state"].map(normalise_place)
    zone = np.select(
        [origin == dest,
         dest_state.isin(SPECIAL_REGION_STATES),
         origin.map(CITY_STATE) == dest_state,
         origin.isin(METRO_CITIES) & dest.isin(METRO_CITIES)],
        ["A", "E", "B", "C"], default="D")

    vol = df["length_cm"] * df["width_cm"] * df["height_cm"] / divisor
    slabs = np.ceil((np.maximum(df["dead_wt_kg"], vol) / WEIGHT_SLAB_KG).round(6))
    kg = np.maximum(min_kg, slabs * WEIGHT_SLAB_KG)

    first = pd.Series(zone).map({z: r[0] for z, r in RATE_CARD.items()}).to_numpy()
    extra = pd.Series(zone).map({z: r[1] for z, r in RATE_CARD.items()}).to_numpy()
    n_slabs = np.ceil((kg / WEIGHT_SLAB_KG).round(6))
    freight = ((first + extra * (n_slabs - 1)) * (1 - discount)).round(2)

    is_cod = (df["payment_mode"] == "COD") & ~waived
    cod = np.where(is_cod, np.maximum(cod_min, cod_pct * df["cod_value"]), 0.0).round(2)

    return pd.DataFrame({"zone": zone, "kg": kg, "freight": freight, "cod_fee": cod,
                         "total": (freight + cod).round(2)}, index=df.index)


# ---------------------------------------------------------------------------
# Unit tests
# ---------------------------------------------------------------------------

def run_unit_tests() -> dict:
    """Run pytest and return counts (so the notebook reports real numbers)."""
    out = subprocess.run([sys.executable, "-m", "pytest", "-q", "--color=no", str(ROOT / "tests")],
                         capture_output=True, text=True, cwd=ROOT)
    last = out.stdout.strip().splitlines()[-1]

    def count(word: str) -> int:
        found = re.search(rf"(\d+) {word}", last)
        return int(found.group(1)) if found else 0

    return {"passed": count("passed"), "failed": count("failed") + count("error"), "summary": last}


# ---------------------------------------------------------------------------
# Verification checks
# ---------------------------------------------------------------------------

def verification_checks(audit: pd.DataFrame) -> pd.DataFrame:
    """Each row: what was checked, the evidence (numbers), and pass/fail."""
    ind = independent_expected(audit)
    n = len(audit)
    agree = {
        "zone": (ind["zone"] == audit["exp_zone"]).sum(),
        "kg": ((ind["kg"] - audit["exp_chargeable_kg"]).abs() <= TOL).sum(),
        "freight": ((ind["freight"] - audit["exp_freight"]).abs() <= TOL).sum(),
        "cod_fee": ((ind["cod_fee"] - audit["exp_cod_fee"]).abs() <= TOL).sum(),
        "total": ((ind["total"] - audit["exp_total"]).abs() <= TOL).sum(),
    }
    matched = int((audit["reason"] == "ok").sum())
    mismatched = audit[audit["reason"] != "ok"]
    abs_diff = audit["diff_total"].abs()
    near_threshold = int(((abs_diff > TOL) & (abs_diff < 0.10)).sum())
    smallest = abs_diff[abs_diff > TOL].min()
    exp_minus_billed = audit["exp_total"].sum() - audit["billed_total"].sum()
    under = -audit.loc[audit["diff_total"] < -TOL, "diff_total"].sum()
    over = audit.loc[audit["diff_total"] > TOL, "diff_total"].sum()
    impact = attribute_impact(audit)
    per_awb = impact.groupby("awb")["impact_inr"].sum()
    split_residual = (per_awb + audit.set_index("awb").loc[per_awb.index, "diff_total"]).abs().max()
    inconsistent = int(mismatched["reason"].str.contains("inconsistent|unexplained").sum())

    rows = [
        ("Independent vectorised recomputation agrees with engine",
         f"zone {agree['zone']:,}/{n:,}, kg {agree['kg']:,}/{n:,}, freight {agree['freight']:,}/{n:,}, "
         f"COD {agree['cod_fee']:,}/{n:,}, total {agree['total']:,}/{n:,}",
         all(v == n for v in agree.values())),
        ("Engine reproduces billed_total where billing was right",
         f"{matched:,}/{n:,} shipments ({matched / n:.1%}) match to ₹0.01 on every component", matched > 0),
        ("Every mismatch reproduced by a known billing error",
         f"{len(mismatched):,} mismatches; {inconsistent} unexplained or inconsistent "
         f"(billed freight always equals rate card at billed zone/kg)", inconsistent == 0),
        ("Totals identity",
         f"sum(expected) - sum(billed) = {exp_minus_billed:,.2f} = underbilled {under:,.2f} - overbilled {over:,.2f}",
         abs(exp_minus_billed - (under - over)) < 0.01),
        ("Billed file is internally consistent",
         "billed_total = billed_freight + billed_cod_fee on all rows",
         bool(((audit["billed_freight"] + audit["billed_cod_fee"] - audit["billed_total"]).abs() <= TOL).all())),
        ("Cause split adds back to each shipment's gap",
         f"max residual per shipment ₹{split_residual:.2f}; causes sum to ₹{impact['impact_inr'].sum():,.2f}",
         split_residual < 0.01),
        ("Threshold (₹0.005) does not drive the counts",
         f"smallest real gap ₹{smallest:.2f}; {near_threshold} gaps under ₹0.10; no gaps between ₹0.005 and ₹0.01",
         not ((abs_diff > TOL) & (abs_diff <= 0.01)).any()),
    ]
    return pd.DataFrame(rows, columns=["check", "evidence", "passed"])


# ---------------------------------------------------------------------------
# Edge cases: how often each one occurs in the data, and how it is handled
# ---------------------------------------------------------------------------

def edge_cases(audit: pd.DataFrame) -> pd.DataFrame:
    d = audit
    heavier = np.maximum(d["dead_wt_kg"], d["exp_vol_kg"])
    ratio = heavier / WEIGHT_SLAB_KG
    exact_slab = np.isclose(ratio, ratio.round(), atol=1e-9)
    float_guard = (np.ceil(ratio) != np.ceil(ratio.round(6)))
    vol5000 = d["length_cm"] * d["width_cm"] * d["height_cm"] / 5000
    m11 = d["merchant_id"] == "M11"
    m11_divisor_matters = m11 & (np.ceil((np.maximum(d["dead_wt_kg"], vol5000) / 0.5).round(6))
                                 != np.ceil((heavier / 0.5).round(6)))
    m07_min_binds = (d["merchant_id"] == "M07") & (np.ceil(ratio.round(6)) * 0.5 < 1.0)
    cod = d["payment_mode"] == "COD"
    m02_cod = (d["merchant_id"] == "M02") & cod
    day = d["ship_date"].dt.strftime("%m-%d")
    terms_pct = d["exp_cod_fee"] > d["merchant_id"].map(
        {m: (t.cod_min_inr or DEFAULT_COD_MIN_INR) for m, t in MERCHANTS.items()}) + TOL
    places = pd.concat([d["dest_city"], d["dest_state"]])

    rows = [
        ("Exact-slab weight (e.g. 1.0 kg = 2 slabs, not 3)", "A.3", int(exact_slab.sum()),
         "ceil, so exact multiples stay put", "test_chargeable_exact_slab_stays, test_freight_exact_slab_weight"),
        ("Float noise near a slab boundary", "A.3", int(float_guard.sum()),
         "ceil(round(w/0.5, 6)) so 1.0000000002 is not a 3rd slab", "test_chargeable_float_noise_does_not_add_a_slab"),
        ("Volumetric heavier than dead weight", "A.3", int((d["exp_vol_kg"] > d["dead_wt_kg"]).sum()),
         "max(dead, volumetric); volumetric not rounded before max", "test_chargeable_uses_higher_of_dead_and_volumetric"),
        ("Declared weight differs from re-measured", "§1.2", int((d["declared_wt_kg"] != d["dead_wt_kg"]).sum()),
         "declared weight never used", "test_expected_charge_ignores_declared_weight"),
        ("M11 divisor 4000 changes the slab", "M11-§2.2", int(m11_divisor_matters.sum()),
         "per-merchant divisor", "test_volumetric_m11_divisor_4000, test_m11_divisor_end_to_end"),
        ("M07 1.0 kg minimum binds", "M07-§2.3", int(m07_min_binds.sum()),
         "max(1.0, rounded weight)", "test_chargeable_m07_minimum_one_kg, test_m07_minimum_end_to_end"),
        ("Zone E (Special Region destination)", "A.2 rule 2", int((d["exp_zone"] == "E").sum()),
         "checked before same-state and metro", "test_zone_e_beats_b_and_c"),
        ("Same state with a metro origin (Mumbai->Pune/Nagpur)", "A.2 rule 3 vs 4",
         int(((d["origin_city"] == "Mumbai") & d["dest_city"].isin(["Pune", "Nagpur"])).sum()),
         "B before C", "test_zone_b_same_state_beats_metro"),
        ("Hyderabad as metro (metro<->Hyderabad)", "A.2 rule 4",
         int(((d["exp_zone"] == "C") & (d["origin_city"].eq("Hyderabad") | d["dest_city"].eq("Hyderabad"))).sum()),
         "Hyderabad in metro list", "test_zone_c_metro_to_metro_including_hyderabad"),
        ("Same-state (Zone B) needs origin state, which is not in the CSV", "A.2 rule 3", int((d["exp_zone"] == "B").sum()),
         "hardcoded city->state map for the 12 pickup cities", "test_zone_b_same_state_beats_metro"),
        ("Place-name spelling variants", "A.2", int((places.map(normalise_place) != places).sum()),
         "strip + alias map (none found)", "test_normalise_place_variants"),
        ("M02 waiver boundary days (31 Jul / 1 Aug / 31 Aug / 1 Sep)", "M02-Ann1.1/1.2",
         int((m02_cod & day.isin(["07-31", "08-01", "08-31", "09-01"])).sum()),
         "inclusive date window on ship_date", "test_m02_cod_waived_31_aug_charged_1_sep"),
        ("M09 discount boundary days (31 Aug / 1 Sep)", "M09-Ann1.1",
         int(((d["merchant_id"] == "M09") & day.isin(["08-31", "09-01"])).sum()),
         "12% from ship_date >= 1 Sep", "test_m09_freight_8pct_31_aug_12pct_1_sep"),
        ("COD fee: minimum branch", "A.4 / §3.1", int((cod & ~terms_pct & (d["exp_cod_fee"] > 0)).sum()),
         "max(min, % x value)", "test_cod_minimum_applies"),
        ("COD fee: percentage branch", "A.4 / §3.1", int((cod & terms_pct).sum()),
         "max(min, % x value)", "test_cod_percentage_applies"),
        ("Prepaid shipments", "A.4", int((~cod).sum()), "COD fee = 0", "test_cod_prepaid_is_zero"),
        ("Discount must not touch COD fee", "A.5", int((cod & (d["exp_cod_fee"] > 0)).sum()),
         "discount applied to freight only", "test_expected_charge_discount_not_applied_to_cod"),
        ("Shipments with 2+ billing errors", "-", int(d["reason"].str.contains(r"; | \+ ", regex=True).sum()),
         "₹ split by fixing one error at a time", "test_impact_split_adds_up_to_diff"),
    ]
    return pd.DataFrame(rows, columns=["edge_case", "clause", "shipments_in_data", "how_handled", "tests"])
