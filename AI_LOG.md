# AI usage log

Tools: **Claude Code** (Claude Opus, in VS Code) to build and test the code, notebooks and drafts; **Gemini API**
(`gemini-3.5-flash`, free tier) as the contract assistant's answer writer. Total time: about 3 h 20 min.

| step | tool | what I asked | what I verified/changed |
|---|---|---|---|
| 1 | Claude Code | Set up the project (venv, `src/` modules, notebooks, tests, CLAUDE.md working rules), profile `shipments.csv`, and summarise the contracts with the exact clause quoted for every rate, rule, override and amendment. | Checked the quotes against the contract file. Decided the billing rules: bill on re-measured dead weight, `ship_date` = booking date, M02/M09 amendment dates, weight order (higher of dead/volumetric → round up to 0.5 kg → minimum). |
| 2 | Claude Code | Encode Schedule A and each merchant's terms in `contracts.py`, every value citing its clause, with dated amendments. | Checked each value against its clause (M11 divisor 4000, M07 1 kg minimum, merchant COD fees, M02 August waiver, M09 12% from 1 Sep). |
| 3 | Claude Code | Implement the billing rules as small functions (volumetric and chargeable weight, zone, freight, COD fee, expected charge) with a unit test per rule. | Required tests for zone precedence (E before B, Mumbai→Pune = B), M11 divisor, M07 minimum, M02 31 Aug vs 1 Sep, M09 8% vs 12%, COD minimum vs %, prepaid = 0, and exact-slab weights. |
| 4 | Claude Code | Recompute the correct charge for all 7,000 shipments, compare with billed, and build a per-shipment audit table with the reason for each difference. | Confirmed every mismatch is explained by a specific billing error and that sum(expected) − sum(billed) = underbilled − overbilled. |
| 5 | Claude Code | Attribute ₹ impact to each root cause, with merchants, dates and shipment counts. | Checked the per-cause amounts add up exactly to the net total, shipment by shipment. |
| 6 | Claude Code | Verify the recomputation independently. | A second, column-wise recomputation agrees with the engine on all 7,000 shipments; the engine reproduces billed totals exactly where billing was right; edge cases counted against the data. |
| 7 | Claude Code | Answer Part 1 in a notebook that only calls `src/` code, sanity checks first. | Reviewed the totals, top-3 merchants (net and gross), root causes and "looks like leakage but isn't" cases. |
| 8 | Claude Code | Build the dispute model: features known at invoice time, compare logistic regression with LightGBM. | Checked no feature uses the target; validated out of time (Jul→Aug, Aug→Jul) and with 5-fold CV; logistic regression won on log loss and is well calibrated. |
| 9 | Claude Code | Score all September shipments and write `september_predictions.csv`; answer Part 2 in a notebook. | Checked 1,800 rows, header `awb,prob`, AWBs match September exactly, probabilities in [0, 1]; reviewed the drivers and ops recommendations. |
| 10 | Claude Code | Design the contract assistant; ask me the design questions first. | Chose clause-level chunks, BM25 + clause-id lookup, billing engine for calculation questions, 5-turn memory, and LLM answers (Claude / OpenAI / Gemini, whichever key is set) with an offline extractive mode. |
| 11 | Claude Code | Build it: clause parsing, retrieval with merchant filtering and amendment links, calculations, "not in documents" handling, citation check, memory, terminal UI and batch mode. | Offline unit tests for parsing, ranking, clause-id lookup, follow-ups, calculations, "not in documents" and citation warnings. |
| 12 | Claude Code + Gemini API | Connect Gemini with the key in a git-ignored `.env`, with model fallback for free-tier limits. | Tested live calls and the fallback across Gemini models. |
| 13 | Gemini API (via the assistant) | Run the 12 contract questions through the assistant. | Checked each answer against the clause text: calculation answers come from the billing engine; RTO and PAN correctly answered "not in documents" (neither is in the contracts). |
| 14 | Claude Code | Draft the design and evaluation answer and the CFO memo. | Checked every figure against the audit and model outputs; kept the memo under 300 words, in my own voice. |
| 15 | Claude Code | Write the README and the assistant's architecture diagrams (Mermaid). | Checked the diagrams match the code and render. |
