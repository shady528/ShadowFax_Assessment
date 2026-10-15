"""Contract terms: Schedule A rate card + per-merchant overrides.

Source of truth: data/contracts_and_rate_card.md
Every value below carries a comment citing its clause.
"""
from dataclasses import dataclass, field, replace
from datetime import date
from typing import Optional


# ---------------------------------------------------------------------------
# Schedule A -- standard rate card and charging rules
# ---------------------------------------------------------------------------

# A.1 Forward freight: zone -> (first 0.5 kg, each additional 0.5 kg), INR excl. GST
RATE_CARD: dict[str, tuple[float, float]] = {
    "A": (32, 14),  # A.1
    "B": (38, 18),  # A.1
    "C": (45, 24),  # A.1
    "D": (52, 28),  # A.1
    "E": (70, 38),  # A.1
}

# A.2 rule 2: "Special Region state (Assam, Meghalaya, Jammu and Kashmir)"
SPECIAL_REGION_STATES = {"Assam", "Meghalaya", "Jammu and Kashmir"}

# A.2 rule 4: "Metro cities (Delhi, Mumbai, Bengaluru, Chennai, Kolkata, Hyderabad)"
METRO_CITIES = {"Delhi", "Mumbai", "Bengaluru", "Chennai", "Kolkata", "Hyderabad"}

# Origin city -> state. Not in the CSV; needed for A.2 rule 3 (same state).
# Origin cities are the merchant warehouse cities in each agreement's preamble.
CITY_STATE = {
    "Mumbai": "Maharashtra",
    "Pune": "Maharashtra",
    "Delhi": "Delhi",
    "Bengaluru": "Karnataka",
    "Jaipur": "Rajasthan",
    "Chennai": "Tamil Nadu",
    "Kolkata": "West Bengal",
    "Hyderabad": "Telangana",
    "Ahmedabad": "Gujarat",
    "Indore": "Madhya Pradesh",
    "Lucknow": "Uttar Pradesh",
    "Kochi": "Kerala",
}

# A.3 Chargeable weight
DEFAULT_VOLUMETRIC_DIVISOR = 5000   # A.3 "L x W x H (cm) / 5000"
WEIGHT_SLAB_KG = 0.5                # A.3 "rounded up to the next 0.5 kg slab"
DEFAULT_MIN_CHARGEABLE_KG = 0.5     # A.3 "minimum chargeable weight is 0.5 kg"

# A.4 COD fee: "higher of INR 30 or 1.75% of the COD value"
DEFAULT_COD_MIN_INR = 30.0          # A.4
DEFAULT_COD_PCT = 0.0175            # A.4

# A.5 Discounts apply to freight only, never to the COD fee.


# ---------------------------------------------------------------------------
# Per-merchant terms
# ---------------------------------------------------------------------------

@dataclass
class Amendment:
    """A dated change to a merchant's terms (from an Annexure)."""
    clause: str                     # e.g. "M09-Ann1.1"
    start: date                     # first ship_date the change applies to (inclusive)
    end: Optional[date] = None      # last ship_date (inclusive); None = open-ended
    changes: dict = field(default_factory=dict)  # field name -> new value


@dataclass
class MerchantTerms:
    merchant_id: str
    name: str
    pickup_city: str
    freight_discount_pct: float = 0.0               # as a fraction, e.g. 0.08
    volumetric_divisor: Optional[float] = None      # None -> Schedule A default
    min_chargeable_kg: Optional[float] = None       # None -> Schedule A default
    cod_min_inr: Optional[float] = None             # None -> Schedule A default
    cod_pct: Optional[float] = None                 # None -> Schedule A default
    cod_waived: bool = False                        # True -> no COD fee (M02 Aug)
    amendments: list[Amendment] = field(default_factory=list)


MERCHANTS: dict[str, MerchantTerms] = {
    "M01": MerchantTerms(
        "M01", "Chandan Home", "Mumbai",            # preamble
        freight_discount_pct=0.08,                  # M01-§2.1
    ),
    "M02": MerchantTerms(
        "M02", "Tara Spices", "Delhi",              # preamble
        freight_discount_pct=0.05,                  # M02-§2.1
        cod_min_inr=25.0, cod_pct=0.015,            # M02-§3.1
        amendments=[
            # M02-Ann1.1: no COD fee for COD shipments booked 1-31 Aug 2026 (inclusive)
            # M02-Ann1.2: §3.1 fee applies again from 1 Sep 2026
            Amendment("M02-Ann1.1", start=date(2026, 8, 1), end=date(2026, 8, 31),
                      changes={"cod_waived": True}),
        ],
    ),
    "M03": MerchantTerms(
        "M03", "Kapas Kids", "Bengaluru",           # preamble
        freight_discount_pct=0.10,                  # M03-§2.1
    ),
    "M04": MerchantTerms(
        "M04", "Moti Jewels", "Jaipur",             # preamble
        freight_discount_pct=0.12,                  # M04-§2.1
        cod_min_inr=40.0, cod_pct=0.02,             # M04-§3.1
    ),
    "M05": MerchantTerms(
        "M05", "Pustak Ghar", "Chennai",            # preamble
        freight_discount_pct=0.06,                  # M05-§2.1
    ),
    "M06": MerchantTerms(
        "M06", "Sitar Sounds", "Kolkata",           # preamble
        freight_discount_pct=0.0,                   # M06-§2.1 "No discount applies."
        cod_min_inr=40.0, cod_pct=0.0125,           # M06-§3.1
    ),
    "M07": MerchantTerms(
        "M07", "Patang Apparel", "Hyderabad",       # preamble
        freight_discount_pct=0.10,                  # M07-§2.1
        min_chargeable_kg=1.0,                      # M07-§2.3
    ),
    "M08": MerchantTerms(
        "M08", "Vaidya Wellness", "Pune",           # preamble
        freight_discount_pct=0.05,                  # M08-§2.1
        cod_min_inr=35.0, cod_pct=0.025,            # M08-§3.1
    ),
    "M09": MerchantTerms(
        "M09", "Lakdi Furniture", "Ahmedabad",      # preamble
        freight_discount_pct=0.08,                  # M09-§2.1
        amendments=[
            # M09-Ann1.1: 12% instead of 8% for shipments booked on/after 1 Sep 2026
            Amendment("M09-Ann1.1", start=date(2026, 9, 1),
                      changes={"freight_discount_pct": 0.12}),
        ],
    ),
    "M10": MerchantTerms(
        "M10", "Rasoi Ware", "Indore",              # preamble
        freight_discount_pct=0.07,                  # M10-§2.1
    ),
    "M11": MerchantTerms(
        "M11", "Shilp Decor", "Lucknow",            # preamble
        freight_discount_pct=0.05,                  # M11-§2.1
        volumetric_divisor=4000,                    # M11-§2.2
    ),
    "M12": MerchantTerms(
        "M12", "Nadi Naturals", "Kochi",            # preamble
        freight_discount_pct=0.10,                  # M12-§2.1
    ),
}


def _is_active(amendment: Amendment, ship_date: date) -> bool:
    if ship_date < amendment.start:
        return False
    if amendment.end is not None and ship_date > amendment.end:
        return False
    return True


def terms_for(merchant_id: str, ship_date: date) -> MerchantTerms:
    """Return the merchant's terms effective on ship_date.

    Applies any amendment whose date window contains ship_date, then fills
    unset fields with the Schedule A defaults, so every field is a concrete value.
    """
    if hasattr(ship_date, "date"):  # accept pandas Timestamp / datetime
        ship_date = ship_date.date()

    terms = replace(MERCHANTS[merchant_id])
    for amendment in terms.amendments:
        if _is_active(amendment, ship_date):
            terms = replace(terms, **amendment.changes)

    if terms.volumetric_divisor is None:
        terms.volumetric_divisor = DEFAULT_VOLUMETRIC_DIVISOR
    if terms.min_chargeable_kg is None:
        terms.min_chargeable_kg = DEFAULT_MIN_CHARGEABLE_KG
    if terms.cod_min_inr is None:
        terms.cod_min_inr = DEFAULT_COD_MIN_INR
    if terms.cod_pct is None:
        terms.cod_pct = DEFAULT_COD_PCT
    return terms
