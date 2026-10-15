"""Part 1 -- leakage audit: expected vs billed per shipment, with a reason.

Sign convention: diff = billed - expected.
  diff < 0  -> underbilled (Shadowfax lost revenue)
  diff > 0  -> overbilled  (merchant was overcharged)
"""
import math
from itertools import combinations
from pathlib import Path

import pandas as pd

from src.billing import cod_fee, expected_charge, freight_charge, volumetric_weight
from src.contracts import (
    DEFAULT_COD_PCT,
    DEFAULT_MIN_CHARGEABLE_KG,
    DEFAULT_VOLUMETRIC_DIVISOR,
    MERCHANTS,
    WEIGHT_SLAB_KG,
    terms_for,
)

TOL = 0.005  # money / weight comparison tolerance


def _same(a: float, b: float) -> bool:
    return abs(a - b) <= TOL


# ---------------------------------------------------------------------------
# Reason rules: each checks one component and, if wrong, tries known error
# patterns in priority order. The first candidate that reproduces the billed
# value is the reason; otherwise the component is flagged "unexplained".
# ---------------------------------------------------------------------------

def zone_reason(row) -> str:
    if row["billed_zone"] == row["exp_zone"]:
        return ""
    if "Hyderabad" in (row["origin_city"], row["dest_city"]) and row["exp_zone"] == "C":
        return f"zone {row['billed_zone']} billed, expected {row['exp_zone']} (Hyderabad not treated as metro, A.2 rule 4)"
    return f"zone {row['billed_zone']} billed, expected {row['exp_zone']}"


def kg_with_errors(row, terms, divisor=None, round_down=False, use_declared=False,
                    ignore_volumetric=False, min_kg=None) -> float:
    """Chargeable weight as a faulty billing system would compute it.

    Each keyword switches on one known billing error; with no keywords this
    reproduces the correct chargeable weight.
    """
    weight = row["declared_wt_kg"] if use_declared else row["dead_wt_kg"]
    if not ignore_volumetric:
        vol = volumetric_weight(row["length_cm"], row["width_cm"], row["height_cm"],
                                divisor or terms.volumetric_divisor)
        weight = max(weight, vol)
    slabs = round(weight / WEIGHT_SLAB_KG, 6)
    slabs = math.floor(slabs) if round_down else math.ceil(slabs)
    return max(min_kg or terms.min_chargeable_kg, slabs * WEIGHT_SLAB_KG)


def weight_errors(terms) -> dict:
    """Known weight errors, in priority order (first match wins).

    Contract-specific overrides that were missed come first, then errors seen
    across all merchants. Divisor 6000 is not in any contract; it is a billing
    error pattern found in the data.
    """
    errors = {}
    if terms.min_chargeable_kg != DEFAULT_MIN_CHARGEABLE_KG:
        errors[f"min {DEFAULT_MIN_CHARGEABLE_KG} kg used instead of {terms.min_chargeable_kg} kg ({terms.merchant_id}-§2.3)"] = \
            {"min_kg": DEFAULT_MIN_CHARGEABLE_KG}
    if terms.volumetric_divisor != DEFAULT_VOLUMETRIC_DIVISOR:
        errors[f"divisor {DEFAULT_VOLUMETRIC_DIVISOR} used instead of {terms.volumetric_divisor:g} ({terms.merchant_id}-§2.2)"] = \
            {"divisor": DEFAULT_VOLUMETRIC_DIVISOR}
    errors["volumetric divisor 6000 used instead of contract divisor (A.3)"] = {"divisor": 6000}
    errors["weight rounded down instead of up to 0.5 kg slab (A.3)"] = {"round_down": True}
    errors["declared weight used instead of re-measured weight (§1.2)"] = {"use_declared": True}
    errors["volumetric weight ignored (A.3)"] = {"ignore_volumetric": True}
    return errors


def weight_reason(row, terms) -> str:
    billed, expected = row["billed_chargeable_kg"], row["exp_chargeable_kg"]
    if _same(billed, expected):
        return ""

    errors = weight_errors(terms)
    # 1) one error on its own
    for label, kwargs in errors.items():
        if _same(billed, kg_with_errors(row, terms, **kwargs)):
            return label
    # 2) two errors together (keys never overlap within a pair except divisor; skip those)
    for (label_a, kw_a), (label_b, kw_b) in combinations(errors.items(), 2):
        if kw_a.keys() & kw_b.keys():
            continue
        if _same(billed, kg_with_errors(row, terms, **kw_a, **kw_b)):
            return f"{label_a} + {label_b}"
    direction = "under" if billed < expected else "over"
    return f"chargeable weight {direction}stated, unexplained"


def freight_reason(row, terms) -> str:
    """Only called when zone and weight are both correct but freight still differs."""
    if _same(row["billed_freight"], row["exp_freight"]):
        return ""
    base = MERCHANTS[terms.merchant_id].freight_discount_pct
    zone, kg = row["exp_zone"], row["exp_chargeable_kg"]
    if base != terms.freight_discount_pct and _same(row["billed_freight"], round(freight_charge(zone, kg, base), 2)):
        return f"old discount {base:.0%} used instead of {terms.freight_discount_pct:.0%} (amendment)"
    if _same(row["billed_freight"], round(freight_charge(zone, kg, 0.0), 2)):
        return "freight discount not applied"
    return "freight differs, unexplained"


def cod_reason(row, terms) -> str:
    if _same(row["billed_cod_fee"], row["exp_cod_fee"]):
        return ""
    if row["payment_mode"] != "COD":
        return "COD fee charged on prepaid shipment (A.4)"
    if terms.cod_waived:
        return "COD fee charged during waiver period (M02-Ann1.1)"
    if _same(row["billed_cod_fee"], terms.cod_min_inr):
        return f"flat INR {terms.cod_min_inr:g} minimum charged, % of COD value ignored ({terms.merchant_id}-§3.1)"
    if _same(row["billed_cod_fee"], round(cod_fee("COD", row["cod_value"], terms.cod_min_inr, DEFAULT_COD_PCT), 2)):
        return "Schedule A COD % used instead of merchant rate"
    return "COD fee differs, unexplained"


def explain(row) -> str:
    terms = terms_for(row["merchant_id"], row["ship_date"])
    reasons = [zone_reason(row), weight_reason(row, terms)]
    zone_and_weight_ok = not any(reasons)
    if zone_and_weight_ok:
        reasons.append(freight_reason(row, terms))
    elif not _same(row["billed_freight"],
                   round(freight_charge(row["billed_zone"], row["billed_chargeable_kg"], terms.freight_discount_pct), 2)):
        # Freight is not even consistent with the billed zone/weight -> extra problem.
        reasons.append("freight inconsistent with billed zone/weight")
    reasons.append(cod_reason(row, terms))
    reasons = [r for r in reasons if r]
    return "; ".join(reasons) if reasons else "ok"


# ---------------------------------------------------------------------------
# ₹ impact per cause: fix one error at a time and measure the change
# ---------------------------------------------------------------------------

def _discount_label(terms) -> str:
    base = MERCHANTS[terms.merchant_id].freight_discount_pct
    if base != terms.freight_discount_pct:
        return f"old discount {base:.0%} used instead of {terms.freight_discount_pct:.0%} (amendment)"
    return "freight discount differs"


def impact_steps(row) -> list[tuple[str, float]]:
    """Split one shipment's gap (expected - billed) into (cause, ₹) pieces.

    Starts from what was billed and corrects one thing at a time, using the
    normal billing functions:
      1. discount  -- re-price billed zone + billed kg with the correct discount
      2. zone      -- move to the correct zone (still at billed kg)
      3. weight    -- move to the correct kg (if two weight errors, fix the first-listed one first)
      4. COD fee   -- correct COD fee
    The pieces add up exactly to exp_total - billed_total. The order is a choice
    (stated here) and only matters for shipments with more than one cause.
    """
    terms = terms_for(row["merchant_id"], row["ship_date"])
    disc = terms.freight_discount_pct

    def price(zone, kg):
        return round(freight_charge(zone, kg, disc), 2)

    steps = []
    step1 = price(row["billed_zone"], row["billed_chargeable_kg"])
    if not _same(step1, row["billed_freight"]):
        steps.append((_discount_label(terms), step1 - row["billed_freight"]))

    step2 = price(row["exp_zone"], row["billed_chargeable_kg"])
    if row["billed_zone"] != row["exp_zone"]:
        steps.append((zone_reason(row), step2 - step1))

    label = weight_reason(row, terms)
    if label:
        parts = label.split(" + ")
        if len(parts) == 2:                       # two weight errors: fix parts[0] first
            remaining = weight_errors(terms)[parts[1]]
            mid = price(row["exp_zone"], kg_with_errors(row, terms, **remaining))
            steps.append((parts[0], mid - step2))
            steps.append((parts[1], row["exp_freight"] - mid))
        else:
            steps.append((label, row["exp_freight"] - step2))

    if not _same(row["billed_cod_fee"], row["exp_cod_fee"]):
        steps.append((cod_reason(row, terms), row["exp_cod_fee"] - row["billed_cod_fee"]))
    return steps


def attribute_impact(audit: pd.DataFrame) -> pd.DataFrame:
    """Long table: one row per (awb, cause) with ₹ impact = expected - billed (positive = underbilled)."""
    rows = []
    for _, r in audit[audit["reason"] != "ok"].iterrows():
        steps = impact_steps(r)
        for cause, inr in steps:
            rows.append({"awb": r["awb"], "merchant_id": r["merchant_id"], "ship_date": r["ship_date"],
                         "cause": cause, "impact_inr": round(inr, 2), "n_causes": len(steps)})
    return pd.DataFrame(rows)


# ---------------------------------------------------------------------------
# Audit table + summaries
# ---------------------------------------------------------------------------

def build_audit(df: pd.DataFrame) -> pd.DataFrame:
    """One row per awb: inputs, billed, expected, per-component flags, diff and reason."""
    expected = df.apply(expected_charge, axis=1, result_type="expand")
    audit = pd.concat([df, expected], axis=1)

    audit["zone_ok"] = audit["billed_zone"] == audit["exp_zone"]
    audit["weight_ok"] = (audit["billed_chargeable_kg"] - audit["exp_chargeable_kg"]).abs() <= TOL
    audit["freight_ok"] = (audit["billed_freight"] - audit["exp_freight"]).abs() <= TOL
    audit["cod_ok"] = (audit["billed_cod_fee"] - audit["exp_cod_fee"]).abs() <= TOL

    audit["diff_freight"] = (audit["billed_freight"] - audit["exp_freight"]).round(2)
    audit["diff_cod_fee"] = (audit["billed_cod_fee"] - audit["exp_cod_fee"]).round(2)
    audit["diff_total"] = (audit["billed_total"] - audit["exp_total"]).round(2)

    audit["status"] = "ok"
    audit.loc[audit["diff_total"] < -TOL, "status"] = "underbilled"
    audit.loc[audit["diff_total"] > TOL, "status"] = "overbilled"

    audit["reason"] = audit.apply(explain, axis=1)
    return audit


def headline(audit: pd.DataFrame) -> pd.DataFrame:
    """Count and INR amount of under/overbilling (amounts shown as positive)."""
    under = audit[audit["status"] == "underbilled"]
    over = audit[audit["status"] == "overbilled"]
    rows = [
        ("underbilled", len(under), round(-under["diff_total"].sum(), 2)),
        ("overbilled", len(over), round(over["diff_total"].sum(), 2)),
        ("net underbilled", len(under) + len(over), round(-audit["diff_total"].sum(), 2)),
    ]
    return pd.DataFrame(rows, columns=["metric", "shipments", "amount_inr"])


def by_merchant(audit: pd.DataFrame) -> pd.DataFrame:
    """Per-merchant under/over/net underbilling (INR), sorted by net underbilling desc."""
    g = audit.groupby("merchant_id")
    out = pd.DataFrame({
        "shipments": g.size(),
        "underbilled_n": g["status"].apply(lambda s: (s == "underbilled").sum()),
        "underbilled_inr": g["diff_total"].apply(lambda d: -d[d < -TOL].sum()),
        "overbilled_n": g["status"].apply(lambda s: (s == "overbilled").sum()),
        "overbilled_inr": g["diff_total"].apply(lambda d: d[d > TOL].sum()),
        "net_underbilled_inr": -g["diff_total"].sum(),
    }).round(2)
    out.insert(0, "name", [MERCHANTS[m].name for m in out.index])
    return out.sort_values("net_underbilled_inr", ascending=False)


def by_reason(audit: pd.DataFrame) -> pd.DataFrame:
    """Count and net INR impact per reason string (excluding 'ok')."""
    bad = audit[audit["reason"] != "ok"]
    out = bad.groupby("reason").agg(
        shipments=("awb", "size"),
        net_underbilled_inr=("diff_total", lambda d: -d.sum()),
        merchants=("merchant_id", lambda m: ",".join(sorted(m.unique()))),
    ).round(2)
    return out.sort_values("net_underbilled_inr", ascending=False)


def by_cause(audit: pd.DataFrame) -> pd.DataFrame:
    """Shipments affected per single root cause.

    A shipment with several causes is counted under each one, so counts here
    add up to more than the number of mismatched shipments. Amounts are left out
    on purpose: a multi-cause diff can't be split between causes without an
    arbitrary rule. Use by_reason for amounts.
    """
    causes = (audit.loc[audit["reason"] != "ok", ["merchant_id", "reason"]]
              .assign(cause=lambda d: d["reason"].str.split(r"; | \+ ", regex=True))
              .explode("cause"))
    out = causes.groupby("cause").agg(
        shipments=("merchant_id", "size"),
        merchants=("merchant_id", lambda m: ",".join(sorted(m.unique()))),
    )
    return out.sort_values("shipments", ascending=False)


AUDIT_COLUMNS = [
    "awb", "ship_date", "merchant_id", "origin_city", "dest_city", "dest_state", "payment_mode", "cod_value",
    "dead_wt_kg", "declared_wt_kg", "length_cm", "width_cm", "height_cm", "exp_vol_kg",
    "billed_zone", "exp_zone", "billed_chargeable_kg", "exp_chargeable_kg",
    "billed_freight", "exp_freight", "diff_freight",
    "billed_cod_fee", "exp_cod_fee", "diff_cod_fee",
    "billed_total", "exp_total", "diff_total", "status", "reason",
]


def write_outputs(audit: pd.DataFrame, out_dir: Path) -> None:
    """Write the per-shipment audit table (expected vs billed per awb, with reason).

    Summaries (by merchant / reason / cause) are shown in the notebook, not saved.
    """
    out_dir.mkdir(exist_ok=True)
    audit[AUDIT_COLUMNS].to_csv(out_dir / "audit_by_awb.csv", index=False)
