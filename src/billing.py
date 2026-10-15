"""Billing rules: recompute the expected charge for one shipment.

Each function implements one rule from Schedule A / the merchant agreement.
Rounding to 2 dp happens only in expected_charge (the final step).
"""
import math

import pandas as pd

from src.contracts import (
    CITY_STATE,
    DEFAULT_MIN_CHARGEABLE_KG,
    METRO_CITIES,
    RATE_CARD,
    SPECIAL_REGION_STATES,
    WEIGHT_SLAB_KG,
    terms_for,
)

# Spelling variants we might see in place names -> canonical form.
# (None found in shipments.csv today; kept so a new variant fails loudly-ish, not silently.)
PLACE_ALIASES = {
    "Bangalore": "Bengaluru",
    "New Delhi": "Delhi",
    "Bombay": "Mumbai",
    "Calcutta": "Kolkata",
    "Madras": "Chennai",
    "Cochin": "Kochi",
    "J&K": "Jammu and Kashmir",
    "Jammu & Kashmir": "Jammu and Kashmir",
}


def normalise_place(name: str) -> str:
    """Trim spaces and map known spelling variants to the canonical name."""
    name = " ".join(str(name).split())
    return PLACE_ALIASES.get(name, name)


def _slabs(weight_kg: float, slab_kg: float = WEIGHT_SLAB_KG) -> int:
    """Number of slabs, rounding up. round(..., 6) guards against float noise (e.g. 1.0000000002)."""
    return math.ceil(round(weight_kg / slab_kg, 6))


def volumetric_weight(length_cm: float, width_cm: float, height_cm: float, divisor: float) -> float:
    """Volumetric weight in kg = L x W x H (cm) / divisor.  [Schedule A.3; M11-§2.2]"""
    return length_cm * width_cm * height_cm / divisor


def chargeable_weight(dead_wt_kg: float, vol_wt_kg: float,
                      slab_kg: float = WEIGHT_SLAB_KG, min_kg: float = DEFAULT_MIN_CHARGEABLE_KG) -> float:
    """max(dead, volumetric), rounded UP to the next slab, floored at min_kg.  [A.3; M07-§2.3]"""
    heavier = max(dead_wt_kg, vol_wt_kg)
    rounded = _slabs(heavier, slab_kg) * slab_kg
    return max(min_kg, rounded)


def zone_with_rule(origin_city: str, dest_city: str, dest_state: str) -> tuple[str, str]:
    """Zone A-E and the Schedule A.2 rule that decided it (first match wins)."""
    origin_city = normalise_place(origin_city)
    dest_city = normalise_place(dest_city)
    dest_state = normalise_place(dest_state)

    if origin_city == dest_city:
        return "A", "A.2 rule 1 (same city)"
    if dest_state in SPECIAL_REGION_STATES:
        return "E", "A.2 rule 2 (Special Region destination)"
    if CITY_STATE[origin_city] == dest_state:
        return "B", "A.2 rule 3 (same state)"
    if origin_city in METRO_CITIES and dest_city in METRO_CITIES:
        return "C", "A.2 rule 4 (both Metro cities)"
    return "D", "A.2 rule 5 (all other shipments)"


def resolve_zone(origin_city: str, dest_city: str, dest_state: str) -> str:
    """Zone A-E using the first matching rule in Schedule A.2 order."""
    return zone_with_rule(origin_city, dest_city, dest_state)[0]


def freight_charge(zone: str, chargeable_kg: float, discount_pct: float) -> float:
    """Schedule A.1 freight for zone/weight, less the merchant discount (A.5). Unrounded."""
    first, additional = RATE_CARD[zone]
    slabs = _slabs(chargeable_kg)
    gross = first + additional * (slabs - 1)
    return gross * (1 - discount_pct)


def cod_fee(payment_mode: str, cod_value: float, cod_min_inr: float, cod_pct: float,
            waived: bool = False) -> float:
    """max(cod_min_inr, cod_pct x cod_value) for COD; 0 for prepaid or waived.  [A.4 / §3.1]"""
    if payment_mode != "COD" or waived:
        return 0.0
    return max(cod_min_inr, cod_pct * cod_value)


def expected_charge(row) -> dict:
    """Expected billing for one shipment row.

    Uses dead_wt_kg (re-measured, final per §1.2) and ship_date as the booked date.
    Freight and COD fee are each rounded to 2 dp here (the final step); total is their sum.
    """
    terms = terms_for(row["merchant_id"], pd.Timestamp(row["ship_date"]))

    vol_kg = volumetric_weight(row["length_cm"], row["width_cm"], row["height_cm"],
                               terms.volumetric_divisor)
    kg = chargeable_weight(row["dead_wt_kg"], vol_kg, WEIGHT_SLAB_KG, terms.min_chargeable_kg)
    zone = resolve_zone(row["origin_city"], row["dest_city"], row["dest_state"])

    freight = round(freight_charge(zone, kg, terms.freight_discount_pct), 2)
    cod = round(cod_fee(row["payment_mode"], row["cod_value"],
                        terms.cod_min_inr, terms.cod_pct, terms.cod_waived), 2)

    return {
        "exp_zone": zone,
        "exp_vol_kg": vol_kg,
        "exp_chargeable_kg": kg,
        "exp_freight": freight,
        "exp_cod_fee": cod,
        "exp_total": round(freight + cod, 2),
    }
