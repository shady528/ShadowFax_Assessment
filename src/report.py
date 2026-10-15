"""Reporting helpers for the notebooks: money formatting and Part 1 summaries.

These only aggregate the audit table from src/audit.py; no billing rules live here.
Sign convention (from src/audit.py): diff_total = billed - expected
  underbilled: expected > billed by more than TOL  (diff_total < -TOL)
  overbilled:  expected < billed by more than TOL  (diff_total > +TOL)
"""
import pandas as pd

from src.audit import TOL, attribute_impact, by_merchant, kg_with_errors
from src.billing import cod_fee, freight_charge
from src.contracts import MERCHANTS, terms_for

MULTI_CAUSE = r"; | \+ "   # separators used in audit 'reason' when a row has 2+ causes


# ---------------------------------------------------------------------------
# Money formatting
# ---------------------------------------------------------------------------

def inr_indian(x: float) -> str:
    """₹1,23,456.78 -- Indian grouping (last 3 digits, then pairs)."""
    sign = "-" if x < 0 else ""
    whole, frac = f"{abs(x):.2f}".split(".")
    if len(whole) > 3:
        head, tail = whole[:-3], whole[-3:]
        pairs = []
        while len(head) > 2:
            pairs.insert(0, head[-2:])
            head = head[:-2]
        pairs.insert(0, head)
        whole = ",".join(pairs) + "," + tail
    return f"{sign}₹{whole}.{frac}"


def inr_western(x: float) -> str:
    """₹123,456.78 -- international grouping."""
    sign = "-" if x < 0 else ""
    return f"{sign}₹{abs(x):,.2f}"


def signed(x: float) -> str:
    """Net amount in words: positive = underbilled, negative = overbilled."""
    return f"underbilled {inr_indian(x)}" if x >= 0 else f"overbilled {inr_indian(-x)}"


def top3(table: pd.DataFrame, col: str, fmt=inr_indian) -> str:
    """'M00 ₹1,234.56; M00 ₹987.65; M00 ₹543.21' from the first 3 rows of table[col]."""
    return "; ".join(f"{m} {fmt(v)}" for m, v in table[col].head(3).items())


# ---------------------------------------------------------------------------
# Q1-Q5: totals and merchant ranking
# ---------------------------------------------------------------------------

def totals(audit: pd.DataFrame) -> dict:
    """Underbilled / overbilled count and ₹ (both as positive amounts)."""
    under = audit[audit["diff_total"] < -TOL]
    over = audit[audit["diff_total"] > TOL]
    return {
        "underbilled_inr": round(-under["diff_total"].sum(), 2),
        "underbilled_n": len(under),
        "overbilled_inr": round(over["diff_total"].sum(), 2),
        "overbilled_n": len(over),
    }


def merchant_rankings(audit: pd.DataFrame) -> tuple[pd.DataFrame, pd.DataFrame]:
    """(ranked by NET underbilling, ranked by GROSS underbilling)."""
    net = by_merchant(audit)
    gross = net.sort_values("underbilled_inr", ascending=False)
    return net, gross


# ---------------------------------------------------------------------------
# Root causes
# ---------------------------------------------------------------------------

CAUSE_TEXT = {
    "zone D billed, expected C (Hyderabad not treated as metro, A.2 rule 4)":
        "Zone: Hyderabad<->metro routes billed Zone D instead of C (Hyderabad is a metro, A.2 rule 4)",
    "weight rounded down instead of up to 0.5 kg slab (A.3)":
        "Weight: chargeable weight rounded DOWN to the 0.5 kg slab instead of up (A.3)",
    "min 0.5 kg used instead of 1.0 kg (M07-§2.3)":
        "Weight: M07 billed with the 0.5 kg minimum instead of its 1.0 kg minimum (M07-§2.3)",
    "volumetric divisor 6000 used instead of contract divisor (A.3)":
        "Weight: volumetric weight computed with divisor 6000 (no contract uses 6000; A.3 = 5000)",
    "flat INR 35 minimum charged, % of COD value ignored (M08-§3.1)":
        "COD: M08 always billed the flat INR 35 minimum, never 2.5% of COD value (M08-§3.1)",
    "divisor 5000 used instead of 4000 (M11-§2.2)":
        "Weight: M11 volumetric weight used divisor 5000 instead of its 4000 (M11-§2.2)",
    "old discount 8% used instead of 12% (amendment)":
        "Discount: M09 Sep shipments billed at the old 8% discount, not 12% (M09-Ann1.1)",
    "volumetric weight ignored (A.3)":
        "Weight: volumetric weight ignored, dead weight billed alone (A.3)",
}


def _months(dates: pd.Series) -> str:
    order = ["Jul", "Aug", "Sep"]
    return ",".join(sorted(dates.dt.strftime("%b").unique(), key=order.index))


def _merchant_counts(ids: pd.Series) -> str:
    """'M05 452' or 'M01 56, M02 56, ...' -- shipments per merchant."""
    return ", ".join(f"{m} {n}" for m, n in ids.value_counts().sort_index().items())


def root_causes(audit: pd.DataFrame) -> pd.DataFrame:
    """One row per root cause, clusters C1..Cn ordered by shipments affected.

    ₹ comes from attribute_impact(): each shipment's gap is split across its causes
    by fixing one error at a time, so net_inr adds up exactly to total net underbilling.
    Positive = underbilled (Shadowfax lost revenue), negative = overbilled.
    """
    impact = attribute_impact(audit)
    order = impact["cause"].value_counts().index
    rows = []
    for i, cause in enumerate(order, start=1):
        hit = impact[impact["cause"] == cause]
        rows.append({
            "cluster": f"C{i}",
            "description": CAUSE_TEXT.get(cause, cause),
            "merchants": _merchant_counts(hit["merchant_id"]),
            "first_date": hit["ship_date"].min().date(),
            "last_date": hit["ship_date"].max().date(),
            "months": _months(hit["ship_date"]),
            "shipments": len(hit),
            "with_other_causes": int((hit["n_causes"] > 1).sum()),
            "underbilled_inr": round(hit.loc[hit["impact_inr"] > 0, "impact_inr"].sum(), 2),
            "overbilled_inr": round(-hit.loc[hit["impact_inr"] < 0, "impact_inr"].sum(), 2),
            "net_inr": round(hit["impact_inr"].sum(), 2),
            "avg_inr_per_shipment": round(hit["impact_inr"].mean(), 2),
            "example_awb": hit.sort_values("ship_date")["awb"].iloc[0],
            "cause": cause,
        })
    return pd.DataFrame(rows).set_index("cluster")


def multi_cause_rows(audit: pd.DataFrame) -> pd.DataFrame:
    return audit[audit["reason"].str.contains(MULTI_CAUSE, regex=True)]


def root_cause_lines(causes: pd.DataFrame) -> list[str]:
    """One copy-paste line per cause."""
    return [
        f"{cid}. {c['description']}. "
        f"Affects {c['merchants']}; ship dates {c['first_date']:%d %b} - {c['last_date']:%d %b %Y}. "
        f"{c['shipments']:,} shipments ({c['with_other_causes']} also have another cause). "
        f"Impact: {signed(c['net_inr'])} (avg {inr_indian(abs(c['avg_inr_per_shipment']))}/shipment). "
        f"e.g. {c['example_awb']}"
        for cid, c in causes.iterrows()
    ]


def _freight(row, zone=None, **errors) -> float:
    """Freight for this row with its correct terms, optionally in another zone or with one weight error."""
    terms = terms_for(row["merchant_id"], row["ship_date"])
    kg = kg_with_errors(row, terms, **errors)
    return round(freight_charge(zone or row["exp_zone"], kg, terms.freight_discount_pct), 2)


def not_leakage(audit: pd.DataFrame) -> pd.DataFrame:
    """Patterns that look like leakage but are billed correctly per contract.

    Only correctly billed shipments where a naive rule would give a different
    charge are counted. correct_minus_naive_inr = correct charge - naive charge.
    Patterns with no such shipments are dropped.
    """
    ok = audit[audit["reason"] == "ok"]

    def naive(rows: pd.DataFrame, **kwargs) -> tuple[int, float]:
        gaps = pd.Series([r["exp_freight"] - _freight(r, **kwargs) for _, r in rows.iterrows()], dtype=float)
        changed = gaps.abs() > TOL
        return int(changed.sum()), round(gaps[changed].sum(), 2)

    m02 = audit[(audit["merchant_id"] == "M02") & (audit["payment_mode"] == "COD")
                & audit["ship_date"].between("2026-08-01", "2026-08-31") & (audit["billed_cod_fee"] == 0)]
    m02_terms = MERCHANTS["M02"]
    m02_fee = round(sum(cod_fee("COD", v, m02_terms.cod_min_inr, m02_terms.cod_pct) for v in m02["cod_value"]), 2)

    candidates = [
        ("M02 COD fee = ₹0 in August", (len(m02), -m02_fee),
         "the normal M02 COD fee (M02-§3.1)",
         "Waived by M02-Ann1.1 for COD shipments booked 1-31 Aug 2026; fee resumes 1 Sep (Ann1.2)."),
        ("Billed weight above declared weight", naive(ok, use_declared=True),
         "pricing on declared weight",
         "Re-measured values are final for billing (§1.2 in every agreement)."),
        ("Volumetric weight above dead weight", naive(ok, ignore_volumetric=True),
         "pricing on dead weight only",
         "Chargeable weight = higher of dead and volumetric (A.3)."),
        ("M11 heavier volumetric weights", naive(ok[ok["merchant_id"] == "M11"], divisor=5000),
         "Schedule A divisor 5000",
         "M11's contract uses divisor 4000 (M11-§2.2)."),
        ("M07 light parcels billed at 1.0 kg", naive(ok[ok["merchant_id"] == "M07"], min_kg=0.5),
         "Schedule A 0.5 kg minimum",
         "M07's contract minimum is 1.0 kg (M07-§2.3)."),
        ("Mumbai->Pune billed Zone B, not C (Mumbai is a metro)",
         naive(ok[(ok["origin_city"] == "Mumbai") & (ok["dest_city"] == "Pune")], zone="C"),
         "Zone C pricing",
         "Same state is checked before metro (A.2 rule 3 before rule 4); Pune is not a metro anyway."),
    ]
    rows = [(name, n, inr, meaning, why) for name, (n, inr), meaning, why in candidates if n > 0]
    return pd.DataFrame(rows, columns=["pattern", "shipments", "correct_minus_naive_inr", "naive_rule", "why_not_leakage"])


def not_leakage_lines(table: pd.DataFrame) -> list[str]:
    return [
        f"{r['pattern']}: {r['shipments']:,} correctly billed shipments "
        f"(correct charge is {inr_indian(abs(r['correct_minus_naive_inr']))} "
        f"{'more' if r['correct_minus_naive_inr'] > 0 else 'less'} than {r['naive_rule']}). {r['why_not_leakage']}"
        for _, r in table.iterrows()
    ]


# ---------------------------------------------------------------------------
# Q6: sanity checks
# ---------------------------------------------------------------------------

def sanity_checks(audit: pd.DataFrame) -> pd.DataFrame:
    t = totals(audit)
    exp_minus_billed = round(audit["exp_total"].sum() - audit["billed_total"].sum(), 2)
    n_mismatch = t["underbilled_n"] + t["overbilled_n"]
    n_sole = (audit["reason"].ne("ok") & ~audit["reason"].str.contains(MULTI_CAUSE, regex=True)).sum()

    checks = [
        ("sum(expected) - sum(billed) == underbilled - overbilled",
         abs(exp_minus_billed - (t["underbilled_inr"] - t["overbilled_inr"])) < 0.005),
        ("per-merchant net sums to overall net",
         abs(by_merchant(audit)["net_underbilled_inr"].sum() - exp_minus_billed) < 0.01),
        ("billed_total == billed_freight + billed_cod_fee (all rows)",
         ((audit["billed_freight"] + audit["billed_cod_fee"] - audit["billed_total"]).abs() <= TOL).all()),
        ("every mismatch has a known cause (0 'unexplained')",
         not audit["reason"].str.contains("unexplained").any()),
        ("₹ attributed to causes sums to total net underbilling",
         abs(attribute_impact(audit)["impact_inr"].sum() - exp_minus_billed) < 0.01),
        ("status 'ok' <=> reason 'ok'",
         ((audit["status"] == "ok") == (audit["reason"] == "ok")).all()),
        ("sole-cause rows + multi-cause rows == all mismatched rows",
         n_sole + len(multi_cause_rows(audit)) == n_mismatch),
    ]
    return pd.DataFrame(checks, columns=["check", "passed"])
