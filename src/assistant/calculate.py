"""Calculation questions ("what's the COD fee for M02 on a ₹2,000 order on 15 Aug?").

Numbers come from the billing engine (src/billing.py + src/contracts.py), never
from an LLM. Parameters are read from the question with simple patterns; if a
needed one is missing, the assistant says what it needs instead of guessing.
"""
import re
from dataclasses import dataclass, field
from datetime import date

import pandas as pd

from src.billing import chargeable_weight, cod_fee, freight_charge, normalise_place, volumetric_weight, zone_with_rule
from src.contracts import (
    CITY_STATE, DEFAULT_MIN_CHARGEABLE_KG, DEFAULT_VOLUMETRIC_DIVISOR, MERCHANTS, RATE_CARD,
    WEIGHT_SLAB_KG, terms_for,
)
from src.contracts import _is_active
from src.assistant.clauses import ROOT

YEAR = 2026
MONTHS = {m: i for i, m in enumerate(["jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec"], 1)}
CALC_WORDS = re.compile(r"\b(how much|calculate|compute|charge|charged|cost|fee|freight|bill|billed|price|total)\b", re.I)


def _city_states() -> dict[str, str]:
    """City -> state for pickup cities (contracts) and delivery cities seen in shipments.csv."""
    states = dict(CITY_STATE)
    path = ROOT / "data" / "shipments.csv"
    if path.exists():
        pairs = pd.read_csv(path, usecols=["dest_city", "dest_state"]).drop_duplicates()
        states.update(dict(zip(pairs["dest_city"], pairs["dest_state"])))
    return states


CITY_STATE_ALL = _city_states()


@dataclass
class CalcRequest:
    merchant_id: str
    ship_date: date | None = None
    date_assumed: bool = False
    dead_wt_kg: float | None = None
    dims_cm: tuple[float, float, float] | None = None
    payment_mode: str | None = None          # "COD" / "Prepaid"
    cod_value: float | None = None
    origin_city: str = ""
    dest_city: str | None = None
    notes: list[str] = field(default_factory=list)


# ---------------------------------------------------------------------------
# Reading parameters from the question
# ---------------------------------------------------------------------------

def _date(text: str) -> tuple[date | None, bool]:
    t = text.lower()
    m = re.search(r"\b(20\d\d)-(\d\d)-(\d\d)\b", t)
    if m:
        return date(int(m.group(1)), int(m.group(2)), int(m.group(3))), False
    m = re.search(r"\b(\d{1,2})(?:st|nd|rd|th)?\s+(jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec)[a-z]*\b", t) \
        or re.search(r"\b(jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec)[a-z]*\s+(\d{1,2})(?:st|nd|rd|th)?\b", t)
    if m:
        a, b = m.groups()
        day, mon = (a, b) if a.isdigit() else (b, a)
        return date(YEAR, MONTHS[mon[:3]], int(day)), False
    m = re.search(r"\b(january|february|march|april|may|june|july|august|september|october|november|december)\b", t)
    if m:                                                        # month only -> the 15th, flagged as assumed
        return date(YEAR, MONTHS[m.group(1)[:3]], 15), True
    return None, False


def _amount(text: str) -> float | None:
    m = re.search(r"(?:₹|rs\.?|inr)\s*([\d,]+(?:\.\d+)?)", text, re.I) \
        or re.search(r"([\d,]+(?:\.\d+)?)\s*(?:₹|rs\.?|inr|rupees)", text, re.I) \
        or re.search(r"cod (?:value|amount|order)?\s*(?:of)?\s*([\d,]+(?:\.\d+)?)", text, re.I)
    return float(m.group(1).replace(",", "")) if m else None


def _city(text: str, prefix: str) -> str | None:
    for city in sorted(CITY_STATE_ALL, key=len, reverse=True):
        if re.search(rf"\b{prefix}\s+{re.escape(city)}\b", text, re.I):
            return city
    for alias, city in [("bangalore", "Bengaluru"), ("bombay", "Mumbai"), ("new delhi", "Delhi"), ("calcutta", "Kolkata")]:
        if re.search(rf"\b{prefix}\s+{alias}\b", text, re.I):
            return city
    return None


def parse_request(question: str, merchant_id: str) -> CalcRequest:
    req = CalcRequest(merchant_id=merchant_id, origin_city=MERCHANTS[merchant_id].pickup_city)
    req.ship_date, req.date_assumed = _date(question)
    m = re.search(r"(\d+(?:\.\d+)?)\s*(?:kg|kgs|kilo)", question, re.I)
    req.dead_wt_kg = float(m.group(1)) if m else None
    m = re.search(r"(\d+(?:\.\d+)?)\s*[x×*]\s*(\d+(?:\.\d+)?)\s*[x×*]\s*(\d+(?:\.\d+)?)", question, re.I)
    req.dims_cm = tuple(float(g) for g in m.groups()) if m else None
    if re.search(r"\bprepaid\b", question, re.I):
        req.payment_mode = "Prepaid"
    elif re.search(r"\bcod\b|cash on delivery", question, re.I):
        req.payment_mode = "COD"
    req.cod_value = _amount(question) if req.payment_mode == "COD" else None
    req.dest_city = _city(question, "to")
    origin = _city(question, "from")
    if origin and origin != req.origin_city:
        req.notes.append(f"Pickups for {merchant_id} are from {req.origin_city} (preamble); "
                         f"the question says {origin}, so {origin} is used as origin.")
        req.origin_city = origin
    return req


def is_calculation(question: str) -> bool:
    """A calculation needs a charge word plus at least one quantity (₹ amount, kg, or dimensions)."""
    has_quantity = bool(re.search(r"₹|\brs\.?\b|\binr\b|\d\s*(kg|kgs)\b|\d\s*[x×*]\s*\d", question, re.I))
    return bool(CALC_WORDS.search(question)) and has_quantity


# ---------------------------------------------------------------------------
# Calculation with clause references
# ---------------------------------------------------------------------------

def _active_amendments(merchant_id: str, ship_date: date) -> list:
    return [a for a in MERCHANTS[merchant_id].amendments if _is_active(a, ship_date)]


def calculate(req: CalcRequest) -> tuple[list[str], list[str]]:
    """Return (answer lines, clause ids used). Lines ask for missing inputs instead of guessing."""
    mid = req.merchant_id
    lines, cited = list(req.notes), []
    if req.ship_date is None:
        if MERCHANTS[mid].amendments:
            clauses = ", ".join(a.clause for a in MERCHANTS[mid].amendments)
            return [f"I need the booking date: {mid}'s terms change by date ({clauses})."], cited
        req.ship_date = date(YEAR, 9, 1)            # no dated amendments -> any date gives the same terms
        lines.append(f"No booking date given; {mid} has no dated amendments, so the date doesn't change the answer.")
    if req.date_assumed:
        lines.append(f"No day given; using {req.ship_date:%d %b %Y} (any day in that month gives the same terms).")
    terms = terms_for(mid, req.ship_date)
    amendments = _active_amendments(mid, req.ship_date)
    amended = {k: a.clause for a in amendments for k in a.changes}

    total = 0.0
    wants_freight = req.dead_wt_kg is not None or req.dims_cm is not None
    if wants_freight:
        if req.dest_city is None:
            lines.append("For freight I also need the delivery city (e.g. 'to Pune').")
        elif req.dest_city not in CITY_STATE_ALL:
            lines.append(f"I don't know which state {req.dest_city} is in, so I can't apply the zone rules (A.2).")
        else:
            dead = req.dead_wt_kg or 0.0
            divisor_clause = f"{mid}-§2.2" if terms.volumetric_divisor != DEFAULT_VOLUMETRIC_DIVISOR else "A.3"
            vol = volumetric_weight(*req.dims_cm, terms.volumetric_divisor) if req.dims_cm else 0.0
            min_clause = f"{mid}-§2.3" if terms.min_chargeable_kg != DEFAULT_MIN_CHARGEABLE_KG else "A.3"
            kg = chargeable_weight(dead, vol, WEIGHT_SLAB_KG, terms.min_chargeable_kg)
            zone, rule = zone_with_rule(req.origin_city, req.dest_city, CITY_STATE_ALL[req.dest_city])
            first, extra = RATE_CARD[zone]
            slabs = round(kg / WEIGHT_SLAB_KG)
            gross = first + extra * (slabs - 1)
            disc_clause = amended.get("freight_discount_pct", f"{mid}-§2.1")
            freight = round(freight_charge(zone, kg, terms.freight_discount_pct), 2)
            total += freight
            vol_txt = (f"volumetric {req.dims_cm[0]:g}×{req.dims_cm[1]:g}×{req.dims_cm[2]:g} ÷ {terms.volumetric_divisor:g} "
                       f"= {vol:.3f} kg [{divisor_clause}]") if req.dims_cm else "no dimensions given, so dead weight only"
            lines += [
                f"Chargeable weight: higher of dead {dead:g} kg and {vol_txt}; rounded up to 0.5 kg slab, "
                f"minimum {terms.min_chargeable_kg:g} kg [{min_clause}] → {kg:g} kg [A.3].",
                f"Zone: {req.origin_city} → {req.dest_city} ({CITY_STATE_ALL[req.dest_city]}) = Zone {zone} [{rule}].",
                f"Freight: ₹{first} + ₹{extra} × {slabs - 1} = ₹{gross:.2f} [A.1], less {terms.freight_discount_pct:.0%} "
                f"discount [{disc_clause}, A.5] = ₹{freight:.2f}.",
            ]
            cited += [f"{mid} preamble", "A.1", "A.2", "A.3", "A.5", disc_clause, divisor_clause, min_clause]   # preamble = pickup city

    if req.payment_mode == "Prepaid":
        lines.append("COD fee: ₹0.00 — prepaid shipments carry no COD fee [A.4].")
        cited.append("A.4")
    elif req.payment_mode == "COD":
        cod_clause = f"{mid}-§3.1" if MERCHANTS[mid].cod_min_inr is not None else "A.4"
        if req.cod_value is None:
            lines.append(f"For the COD fee I need the COD value (e.g. 'COD ₹2,000'). Rule: [{cod_clause}].")
        elif terms.cod_waived:
            lines.append(f"COD fee: ₹0.00 — waived for COD shipments booked {req.ship_date:%d %b %Y} [{amended['cod_waived']}].")
            cited += [amended["cod_waived"], cod_clause]
        else:
            fee = round(cod_fee("COD", req.cod_value, terms.cod_min_inr, terms.cod_pct), 2)
            total += fee
            lines.append(f"COD fee: higher of ₹{terms.cod_min_inr:g} or {terms.cod_pct:.2%} × ₹{req.cod_value:,.2f} "
                         f"(= ₹{terms.cod_pct * req.cod_value:,.2f}) → ₹{fee:.2f} [{cod_clause}]. "
                         f"Discounts never apply to the COD fee [A.5].")
            cited += [cod_clause, "A.5"]

    if wants_freight and req.payment_mode and req.dest_city in CITY_STATE_ALL:
        lines.append(f"Total (excl. GST): ₹{total:,.2f}.")
    if len(lines) == len(req.notes):
        lines.append("Tell me the weight/dimensions and delivery city for freight, or the COD value for the COD fee.")
    return lines, list(dict.fromkeys(cited))
