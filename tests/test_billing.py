"""Unit tests: one small test per billing rule. Expected values are hand-computed."""
from datetime import date

import pandas as pd
import pytest

from src.billing import (
    chargeable_weight,
    cod_fee,
    expected_charge,
    freight_charge,
    normalise_place,
    resolve_zone,
    volumetric_weight,
)
from src.contracts import terms_for


# --- A.3 volumetric weight -------------------------------------------------

def test_volumetric_default_divisor():
    assert volumetric_weight(30, 20, 10, 5000) == pytest.approx(1.2)


def test_volumetric_m11_divisor_4000():          # M11-§2.2
    assert volumetric_weight(30, 20, 10, 4000) == pytest.approx(1.5)


# --- A.3 chargeable weight -------------------------------------------------

def test_chargeable_rounds_up_to_next_slab():
    assert chargeable_weight(1.2, 0.0) == 1.5


def test_chargeable_exact_slab_stays():
    assert chargeable_weight(1.0, 0.0) == 1.0


def test_chargeable_float_noise_does_not_add_a_slab():
    # 0.1 + 0.2 + 0.7 = 1.0000000000000002 in floating point
    assert chargeable_weight(0.1 + 0.2 + 0.7, 0.0) == 1.0


def test_chargeable_uses_higher_of_dead_and_volumetric():
    assert chargeable_weight(0.4, 1.2) == 1.5
    assert chargeable_weight(2.3, 1.2) == 2.5


def test_chargeable_default_minimum_half_kg():
    assert chargeable_weight(0.1, 0.05) == 0.5


def test_chargeable_m07_minimum_one_kg():         # M07-§2.3
    assert chargeable_weight(0.3, 0.2, min_kg=1.0) == 1.0
    assert chargeable_weight(1.3, 0.2, min_kg=1.0) == 1.5


# --- A.2 zones (first match wins) ------------------------------------------

def test_zone_a_same_city():
    assert resolve_zone("Mumbai", "Mumbai", "Maharashtra") == "A"


def test_zone_e_special_region():
    assert resolve_zone("Delhi", "Guwahati", "Assam") == "E"
    assert resolve_zone("Mumbai", "Shillong", "Meghalaya") == "E"
    assert resolve_zone("Delhi", "Jammu", "Jammu and Kashmir") == "E"


def test_zone_e_beats_b_and_c():
    # Guwahati -> Dibrugarh are both in Assam (would be B), but E is checked first
    assert resolve_zone("Guwahati", "Dibrugarh", "Assam") == "E"
    # Delhi is a metro, but a Special Region destination is E, not C/D
    assert resolve_zone("Delhi", "Jammu", "Jammu and Kashmir") == "E"


def test_zone_a_beats_e():
    assert resolve_zone("Guwahati", "Guwahati", "Assam") == "A"


def test_zone_b_same_state_beats_metro():
    # Mumbai and Pune are both in Maharashtra -> B, even though Mumbai is a metro
    assert resolve_zone("Mumbai", "Pune", "Maharashtra") == "B"
    assert resolve_zone("Pune", "Mumbai", "Maharashtra") == "B"


def test_zone_c_metro_to_metro_including_hyderabad():
    assert resolve_zone("Delhi", "Mumbai", "Maharashtra") == "C"
    assert resolve_zone("Hyderabad", "Kolkata", "West Bengal") == "C"


def test_zone_d_other():
    assert resolve_zone("Ahmedabad", "Delhi", "Delhi") == "D"   # Ahmedabad not a metro
    assert resolve_zone("Pune", "Delhi", "Delhi") == "D"        # Pune not a metro


def test_normalise_place_variants():
    assert normalise_place(" Bangalore ") == "Bengaluru"
    assert normalise_place("J&K") == "Jammu and Kashmir"
    assert resolve_zone("Mumbai", "Bangalore", "Karnataka") == "C"


# --- A.1 freight + A.5 discount --------------------------------------------

def test_freight_first_slab_only():
    assert freight_charge("A", 0.5, 0.0) == 32


def test_freight_exact_slab_weight():
    # 1.0 kg = 2 slabs (not 3): 32 + 14
    assert freight_charge("A", 1.0, 0.0) == 46


def test_freight_additional_slabs():
    assert freight_charge("D", 1.5, 0.0) == 52 + 2 * 28   # 108


def test_freight_discount_applied_to_freight():
    assert freight_charge("D", 1.5, 0.08) == pytest.approx(99.36)


# --- A.4 / §3.1 COD fee ----------------------------------------------------

def test_cod_prepaid_is_zero():
    assert cod_fee("Prepaid", 0, 30, 0.0175) == 0


def test_cod_minimum_applies():
    assert cod_fee("COD", 1000, 30, 0.0175) == 30            # 1.75% = 17.5 < 30


def test_cod_percentage_applies():
    assert cod_fee("COD", 2800, 30, 0.0175) == pytest.approx(49.0)


def test_cod_waived():                            # M02-Ann1.1
    assert cod_fee("COD", 2800, 25, 0.015, waived=True) == 0


# --- terms_for: amendments by ship_date ------------------------------------

def test_m09_discount_amendment_boundary():       # M09-Ann1.1
    assert terms_for("M09", date(2026, 8, 31)).freight_discount_pct == 0.08
    assert terms_for("M09", date(2026, 9, 1)).freight_discount_pct == 0.12


def test_m02_cod_waiver_boundaries():             # M02-Ann1.1 / Ann1.2
    assert terms_for("M02", date(2026, 7, 31)).cod_waived is False
    assert terms_for("M02", date(2026, 8, 1)).cod_waived is True
    assert terms_for("M02", date(2026, 8, 31)).cod_waived is True
    assert terms_for("M02", date(2026, 9, 1)).cod_waived is False


def test_merchant_overrides_and_defaults():
    m02 = terms_for("M02", date(2026, 9, 1))
    assert (m02.cod_min_inr, m02.cod_pct) == (25.0, 0.015)       # M02-§3.1
    m01 = terms_for("M01", date(2026, 9, 1))
    assert (m01.cod_min_inr, m01.cod_pct) == (30.0, 0.0175)      # A.4 default
    assert terms_for("M11", date(2026, 9, 1)).volumetric_divisor == 4000
    assert terms_for("M07", date(2026, 9, 1)).min_chargeable_kg == 1.0


# --- expected_charge: end-to-end on real rows ------------------------------

def _row(**kw):
    base = dict(merchant_id="M01", ship_date="2026-07-15", origin_city="Mumbai",
                dest_city="Delhi", dest_state="Delhi", dead_wt_kg=0.4, declared_wt_kg=0.4,
                length_cm=10, width_cm=10, height_cm=10, payment_mode="Prepaid", cod_value=0)
    base.update(kw)
    return pd.Series(base)


def test_expected_charge_matches_known_good_row():
    # SFX2700104729: M09 Ahmedabad->Delhi, 0.37 kg, 16x12x11, Jul, prepaid. Billed 47.84.
    row = _row(merchant_id="M09", ship_date="2026-07-29", origin_city="Ahmedabad",
               dead_wt_kg=0.37, length_cm=16, width_cm=12, height_cm=11)
    out = expected_charge(row)
    assert out["exp_zone"] == "D"
    assert out["exp_chargeable_kg"] == 0.5
    assert out["exp_freight"] == 47.84          # 52 * 0.92
    assert out["exp_total"] == 47.84


def test_expected_charge_discount_not_applied_to_cod():
    # M01 Mumbai->Delhi (C), 0.5 kg, COD 2800: freight 45*0.92=41.40, COD 1.75%*2800=49.00
    out = expected_charge(_row(payment_mode="COD", cod_value=2800))
    assert out["exp_freight"] == 41.40
    assert out["exp_cod_fee"] == 49.00
    assert out["exp_total"] == 90.40


def test_expected_charge_ignores_declared_weight():  # §1.2 re-measured is final
    out = expected_charge(_row(dead_wt_kg=1.2, declared_wt_kg=0.3))
    assert out["exp_chargeable_kg"] == 1.5


def test_m02_cod_waived_31_aug_charged_1_sep():   # M02-Ann1.1 / Ann1.2, M02-§3.1
    row = dict(merchant_id="M02", origin_city="Delhi", dest_city="Mumbai", dest_state="Maharashtra",
               payment_mode="COD", cod_value=2000)
    assert expected_charge(_row(ship_date="2026-07-31", **row))["exp_cod_fee"] == 30.00   # 1.5% of 2000
    assert expected_charge(_row(ship_date="2026-08-01", **row))["exp_cod_fee"] == 0.00
    assert expected_charge(_row(ship_date="2026-08-31", **row))["exp_cod_fee"] == 0.00
    assert expected_charge(_row(ship_date="2026-09-01", **row))["exp_cod_fee"] == 30.00
    small = dict(row, cod_value=1000)                                                     # 1.5% = 15 < 25
    assert expected_charge(_row(ship_date="2026-09-01", **small))["exp_cod_fee"] == 25.00


def test_m09_freight_8pct_31_aug_12pct_1_sep():   # M09-§2.1, M09-Ann1.1
    row = dict(merchant_id="M09", origin_city="Ahmedabad")   # -> Delhi, zone D, 0.5 kg, base 52
    assert expected_charge(_row(ship_date="2026-08-31", **row))["exp_freight"] == 47.84   # 52 * 0.92
    assert expected_charge(_row(ship_date="2026-09-01", **row))["exp_freight"] == 45.76   # 52 * 0.88


def test_m11_divisor_end_to_end():                # M11-§2.2
    # 40x25x10 = 10000 cm3: /4000 = 2.5 kg (with /5000 it would be 2.0 kg)
    row = _row(merchant_id="M11", origin_city="Lucknow", dead_wt_kg=0.5, length_cm=40, width_cm=25, height_cm=10)
    assert expected_charge(row)["exp_chargeable_kg"] == 2.5


def test_m07_minimum_end_to_end():                # M07-§2.3
    row = _row(merchant_id="M07", origin_city="Hyderabad", dead_wt_kg=0.2)
    out = expected_charge(row)
    assert out["exp_chargeable_kg"] == 1.0
    assert out["exp_freight"] == 62.10            # Hyderabad->Delhi C: (45 + 24) * 0.90
