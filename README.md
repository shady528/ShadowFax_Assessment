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
_TODO_

## What I'd do next
_TODO_
