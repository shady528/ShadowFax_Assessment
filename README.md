# Shadowfax Business Finance — Take-home

## Setup
```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
python -m ipykernel install --user --name shadowfax
```

## How to run
- Tests: `pytest -q`
- Part 1 (leakage audit): `notebooks/01_leakage_audit.ipynb` — answers Q1–Q6, ends with a copy-paste "PART 1 ANSWERS" block
- Part 2 (dispute model): `notebooks/02_dispute_model.ipynb` → `outputs/september_predictions.csv` (header `awb,prob`, 1,800 rows); ends with a copy-paste "PART 2 ANSWERS" block
- Part 3 (contract assistant): `python -m src.assistant` — interactive terminal (/help, /sources, /history, /clear, /provider, /exit); `python -m src.assistant --questions questions.txt` answers a list and writes `outputs/contract_answers.md`. Uses Claude, OpenAI or Gemini — whichever key is set (`ANTHROPIC_API_KEY`, `OPENAI_API_KEY`, `GEMINI_API_KEY`) — else quotes the matching clauses. Full guide and architecture diagrams: [`src/assistant/README.md`](src/assistant/README.md).

## Approach
### Part 1 — Leakage audit
1. `src/contracts.py` holds Schedule A and every merchant override, each with its clause. `terms_for(merchant, ship_date)` applies dated amendments (M02 Aug COD waiver, M09 12% from 1 Sep) and fills Schedule A defaults.
2. `src/billing.py` has one function per rule (volumetric weight → chargeable weight → zone → freight → COD fee); `expected_charge(row)` combines them.
3. `src/audit.py` compares billed vs expected per awb, per component (zone, chargeable kg, freight, COD fee, total), and attaches a reason. Reasons come from testing known error patterns against the billed value (single errors first, then pairs), so each label is reproducible, not guessed.
4. Output: `outputs/audit_by_awb.csv` — the full audit table (expected vs billed per awb, with the reason for each difference). Summaries by merchant, reason and cause are shown in the notebook.

## Assumptions
- Billing weight is `dead_wt_kg` + measured dimensions (re-measured values are final, §1.2 in every agreement); `declared_wt_kg` is never used.
- `ship_date` is the "booked" date for amendments.
- Chargeable weight = max(dead, volumetric) → round **up** to the next 0.5 kg (`ceil(round(w/0.5, 6))` to guard float noise) → apply minimum (0.5 kg; 1.0 kg for M07). Volumetric weight is not rounded before comparing.
- Freight = first + additional × (slabs − 1), then × (1 − discount). Discount never applies to the COD fee (A.5).
- Freight and COD fee are each rounded to 2 dp, then summed. Comparisons use a 0.005 tolerance.
- Origin state is not in the CSV; a hardcoded city → state map for the 12 pickup cities is used for zone B (A.2 rule 3).
- `dest_city` / `dest_state` were checked for spelling variants (none found); a small alias map normalises them anyway.
- Dispute model features use only what is known when the invoice goes out (shipment, billed values, contract-expected values). September's M09 overcharges (~₹5) are smaller than any overcharge seen in training (min ₹6.30); the model treats them like other overcharges.
- Reason labels are an attribution, not part of the amount. Where a row matches more than one error pattern, the first in a fixed priority order wins (missed contract override → divisor 6000 → rounding down → declared weight → volumetric ignored). Amounts don't depend on this.

## Results

### Part 1 — Leakage audit (Jul–Sep 2026, 7,000 shipments; INR excl. GST)

| | Shipments | Amount |
|---|---|---|
| Underbilled | 1,493 | ₹35,986.32 |
| Overbilled | 554 | ₹6,290.93 |
| **Net underbilled** | 2,047 | **₹29,695.39** |

**Top 3 merchants by net underbilling:** M05 ₹12,671.20; M11 ₹6,207.30; M07 ₹5,878.80
(by gross underbilling: M05 ₹12,777.42; M07 ₹8,216.10; M11 ₹6,207.30).

**Root causes** (₹ split across causes, so they add up to the net total; positive = underbilled):

| # | What's wrong in billing | Merchants | Shipments | Net ₹ |
|---|---|---|---|---|
| C1 | Hyderabad↔metro routes billed Zone D instead of C (A.2 rule 4) | M01, M02, M03, M05, M06, M07 | 652 | −7,505.72 (overbilled) |
| C2 | Weight rounded down instead of up to the 0.5 kg slab (A.3) | M05 | 452 | +10,606.96 |
| C3 | 0.5 kg minimum used instead of M07's 1.0 kg (M07-§2.3) | M07 | 402 | +9,187.20 |
| C4 | Volumetric weight computed with divisor 6000 (no contract uses it) | all 12 | 290 | +7,963.04 |
| C5 | Flat ₹35 COD fee, 2.5% of COD value ignored (M08-§3.1) | M08 | 223 | +4,458.75 |
| C6 | Divisor 5000 instead of M11's 4000 (M11-§2.2) | M11 | 153 | +5,422.60 |
| C7 | September billed at the old 8% discount, not 12% (M09-Ann1.1) | M09 | 139 | −719.44 (overbilled) |
| C8 | Volumetric weight ignored (A.3) | M05 | 5 | +282.00 |

**Checks:** an independent column-wise recomputation agrees with the engine on all 7,000 shipments; the engine
reproduces the billed total exactly on 4,953 shipments; every one of the 2,047 mismatches is explained by a cause
above (0 unexplained); sum(expected) − sum(billed) = underbilled − overbilled; 56 unit tests pass.

### Part 2 — Dispute model

| Validation | Log loss | ROC AUC |
|---|---|---|
| Baseline (July dispute rate for everyone) → August | 0.442 | 0.500 |
| Train July → score August | **0.378** | **0.733** |
| Train August → score July | 0.363 | 0.701 |
| 5-fold CV on Jul+Aug | 0.365 ± 0.006 | 0.731 ± 0.015 |

- Model: L2 logistic regression on 21 features known at invoice time; beat LightGBM out of time (log loss 0.378 vs 0.387).
- Calibration on August: predicted vs actual dispute rate within 2.8 points in every risk quintile.
- September: `outputs/september_predictions.csv`, 1,800 rows, mean predicted dispute rate 19.5% (vs 15.1% in Jul+Aug),
  because 13.4% of September shipments are overcharged vs 6.0% before.
- Drivers (Jul+Aug): overcharged shipments are disputed 64% of the time vs 12% when billed correctly; COD 18% vs
  prepaid 12%; billed weight far above declared raises disputes; merchants range from M12 6% to M07 30%.

### Part 3 — Contract assistant

215 citable chunks (Schedule A sections, agreement preambles, numbered clauses, annexures). Answers to the 12
questions, checked against the clause text:

| # | Question (short) | Answer | Clause |
|---|---|---|---|
| 1 | Shilp Decor volumetric divisor | 4000 (instead of 5000) | M11-§2.2 |
| 2 | Lakdi Furniture discount, booked 5 Sep 2026 | 12% | M09-Ann1.1 |
| 3 | Pustak Ghar dispute window | 10 days from the invoice date | M05-§5.1 |
| 4 | Liability for a lost Moti Jewels shipment | lower of declared invoice value or ₹8,000 | M04-§6.1 |
| 5 | Tara Spices COD fee, ₹1,500, booked 14 Aug 2026 | ₹0 (August waiver) | M02-Ann1.1 |
| 6 | Minimum chargeable weight above default | M07 Patang Apparel, 1.0 kg | M07-§2.3 |
| 7 | Rasoi Ware payment terms | 45 days from invoice; 1.5%/month on overdue | M10-§4.2 |
| 8 | Kapas Kids RTO charge | not in the documents | — |
| 9 | Vaidya Wellness COD fee, ₹3,000 | ₹75.00 (higher of ₹35 or 2.5%) | M08-§3.1 |
| 10 | Highest freight discount in September 2026 | 12%, tied: M04 and M09 | M04-§2.1, M09-Ann1.1 |
| 11 | Shadowfax's PAN | not in the documents | — |
| 12 | Sitar Sounds termination notice | 45 days' written notice | M06-§7.2 |

Calculation answers (Q5, Q9) come from the billing engine, not the LLM; the assistant's own answers are what the
terminal prints, with clause references.
