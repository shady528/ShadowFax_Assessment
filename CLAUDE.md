# CLAUDE.md — working rules for this repo

Shadowfax Business Finance take-home: billing leakage audit (Part 1), dispute model (Part 2), more parts to follow.

## Rules
1. **All numbers come from deterministic pandas code, never from an LLM.** Any figure in README/notebooks must be produced by code in this repo.
2. **Money:** INR, excluding GST. Keep full precision through every step; round to 2 dp only at the final step per shipment. Keep a clear audit table with expected vs billed per `awb` and the reason for each difference (written to `outputs/`).
3. **Contract terms live in `src/contracts.py`**, each value with a comment citing its clause in `data/contracts_and_rate_card.md` (e.g. `# M09-Ann1.1`).
4. **Every billing rule has a small unit test** in `tests/`.
5. **Prefer simple, readable functions over clever code; no hidden magic.** The code will be walked through and edited live.
6. **Ask before changing data or contract values.**
7. **Logic in `src/`, answers in notebooks.** Each part gets a notebook (`notebooks/0N_*.ipynb`) that only imports from `src/`, runs sanity checks first, then answers each question in its own section and ends with a copy-paste answers block. No business logic in notebook cells.

## Layout
- `data/` — inputs (`shipments.csv`, `contracts_and_rate_card.md`); treat as read-only
- `src/contracts.py` — rate card + merchant terms
- `src/billing.py` — per-shipment billing rules
- `src/audit.py` — expected vs billed per awb + reason
- `src/report.py` — money formatting + Part 1 summaries (aggregation only)
- `src/verify.py` — Q6 evidence: independent column-wise recomputation, verification checks, edge-case coverage
- `src/features.py` — dispute-model features (known at invoice time; no target)
- `src/model.py` — dispute model: logistic regression, validation, calibration, what-if
- `src/assistant/` — Part 3 contract assistant: `clauses.py` (parse contracts into citable clauses), `retrieve.py` (BM25 + clause-id lookup + amendment links), `calculate.py` (calculation questions via the billing engine), `llm.py` (Claude / OpenAI / Gemini / extractive), `assistant.py` (memory + answer flow), `__main__.py` (terminal UI)
- `notebooks/` — `01_leakage_audit.ipynb`, `02_dispute_model.ipynb` (kernel: `shadowfax`)
- `tests/` — pytest
- `outputs/` — `september_predictions.csv` (header `awb,prob`) and audit tables

## Commands
- Activate: `source .venv/bin/activate`
- Tests: `pytest -q`
- Notebooks: `jupyter lab` (kernel "shadowfax")
- Contract assistant: `python -m src.assistant` (uses the first of ANTHROPIC_API_KEY / OPENAI_API_KEY / GEMINI_API_KEY found, else extractive; `--provider` to force; `--questions file.txt` for batch)
