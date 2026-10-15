# Contract assistant (Part 3)

A terminal assistant that answers questions about the 12 merchant agreements and Schedule A
(`data/contracts_and_rate_card.md`). Every answer cites the clause it comes from (e.g. `M01-§4.2`),
and when the documents don't contain the answer it says so plainly instead of guessing.

## Quick start

```bash
source .venv/bin/activate
pip install -r requirements.txt          # includes anthropic, openai, google-genai

# 1. Add an API key (any one) to .env in the repo root -- it is git-ignored
#    GEMINI_API_KEY=your-key-here
#    (see .env.example; a real environment variable also works and takes priority)

# 2. Start the assistant
python -m src.assistant
```

No key? It still works: it runs in **extractive** mode and quotes the matching clauses verbatim.

## Using it

```
$ python -m src.assistant
Shadowfax contract assistant — 215 clauses from Schedule A + 12 agreements
Provider: extractive (model: none (quotes clauses)) | API keys found: gemini
Ask a question, or /help. Memory: last 5 turns.

you> 
────────────────────────────────────────────────────────────────────────────────────────────────
Q: What COD fee applies to a Vaidya Wellness COD shipment worth ₹3,000?
────────────────────────────────────────────────────────────────────────────────────────────────
Answer
  No booking date given; M08 has no dated amendments, so the date doesn't change the answer.
  COD fee: higher of ₹35 or 2.50% × ₹3,000.00 (= ₹75.00) → ₹75.00 [M08-§3.1]. Discounts never
  apply to the COD fee [A.5].
  (Computed with the billing engine from the contract terms.)

Clause references
  [M08-§3.1]  Vaidya Wellness (M08) · Cash on delivery
  [A.5]       Schedule A · Discounts

Answered by: billing engine (src/billing.py), no LLM · calculation
────────────────────────────────────────────────────────────────────────────────────────────────

you> 
────────────────────────────────────────────────────────────────────────────────────────────────
Q: What is Shadowfax's PAN?
────────────────────────────────────────────────────────────────────────────────────────────────
Answer
  NOT IN DOCUMENTS: the contracts and Schedule A don't contain this. Closest clause: M01
  preamble (weak match).

Answered by: retrieval only — no clause matched well enough, no LLM called · not found
────────────────────────────────────────────────────────────────────────────────────────────────

you> bye
```

Each answer is one block: the question, **Answer** (clause ids in brackets), **Clause references** (each cited clause with its merchant and section), any **Warnings**, and **Answered by** (provider and the model that actually answered, or the billing engine). Above is offline mode; with an API key, retrieval answers are written as sentences by the LLM. Calculations and "not in documents" answers don't use the LLM, so they look the same either way. A leading `Q1.` typed before a question is ignored.

| Command | What it does |
|---|---|
| `/help` | list commands |
| `/sources` | clauses retrieved for the last answer, with BM25 scores and matched words |
| `/history` | the questions currently in memory |
| `/clear` | forget the conversation |
| `/provider [name]` | show or switch provider: `claude`, `openai`, `gemini`, `extractive` |
| `/exit`, `/quit`, Ctrl-D | leave |

**Batch mode** (one question per line, each answered on its own):

```bash
python -m src.assistant --questions questions.txt            # writes outputs/contract_answers.md
python -m src.assistant --questions questions.txt --provider gemini --out outputs/answers_gemini.md
```

## Architecture

GitHub (and VS Code with a Mermaid extension) renders the diagrams below.

### 1. Components

```mermaid
flowchart LR
    subgraph IN["Inputs"]
        MD[("contracts_and_rate_card.md<br/>Schedule A + 12 agreements")]
        CSV[("shipments.csv<br/>delivery city → state")]
        ENV[(".env<br/>API keys")]
    end

    subgraph IDX["Index — built once at startup"]
        PARSE["clauses.py<br/>split into 215 citable chunks"]
        LINK["link clauses<br/>references + annexure siblings"]
        BM25["retrieve.py<br/>BM25 index + merchant names"]
        PARSE --> LINK --> BM25
    end

    subgraph CORE["Per question — assistant.py"]
        MEM["Memory<br/>last 5 turns"]
        ROUTE{"Route"}
        CALC["calculate.py<br/>read merchant, date, kg, dims,<br/>COD value, destination"]
        RET["Retriever<br/>BM25 · clause-id lookup ·<br/>per-merchant · linked clauses"]
        GATE{"Strong enough<br/>match?"}
        CHECK["Citation check"]
    end

    subgraph ENGINE["Billing engine (Part 1)"]
        BILL["billing.py"]
        TERMS["contracts.py"]
    end

    subgraph WRITERS["Answer writers — llm.py"]
        CL["Claude"]
        OA["OpenAI"]
        GE["Gemini"]
        EX["Extractive<br/>quote clauses, no LLM"]
    end

    UI["__main__.py<br/>terminal UI · batch mode"]

    MD --> PARSE
    CSV --> CALC
    ENV --> WRITERS
    UI -->|question| MEM --> ROUTE
    ROUTE -->|"one merchant + charge word + ₹/kg/dims"| CALC
    CALC --> BILL --> TERMS
    ROUTE -->|otherwise| RET
    BM25 --> RET
    RET --> GATE
    GATE -->|no| NF["NOT IN DOCUMENTS<br/>(no LLM call)"]
    GATE -->|yes| WRITERS
    WRITERS --> CHECK
    CHECK --> UI
    BILL --> UI
    NF --> UI
```

### 2. Question flow

```mermaid
flowchart TD
    Q(["Question typed"]) --> STRIP["Drop a leading 'Q1.' / '3)'"]
    STRIP --> FU{"Follow-up?<br/>starts with 'and / what about…',<br/>or fewer than 2 own topic words"}
    FU -->|yes| REUSE["Reuse last turn's merchant(s);<br/>if no topic words, add last turn's query"]
    FU -->|no| MER["Detect merchants<br/>by id (M07) or name (Patang)"]
    REUSE --> MER
    MER --> ISCALC{"Exactly one merchant<br/>and a calculation?<br/>(fee / charge / cost + ₹, kg or LxWxH)"}

    ISCALC -->|yes| PARAMS["Read date, weight, dimensions,<br/>COD / prepaid + value, 'to city'"]
    PARAMS --> MISSING{"Inputs missing?"}
    MISSING -->|yes| ASK["Ask for what's missing<br/>(never guess)"]
    MISSING -->|no| ENGINE["Billing engine:<br/>terms for that date → weight → zone →<br/>freight → COD fee; cite clause per step"]

    ISCALC -->|no| CMP{"'Which merchant… / highest /<br/>lowest' and no merchant named?"}
    CMP -->|yes| EACH["Best clause for EACH merchant<br/>+ top 2 Schedule A sections"]
    CMP -->|no| SEARCH["Clause-id lookup + BM25 top 5<br/>(named merchant + Schedule A)"]
    EACH --> LINKS["Add linked clauses<br/>(amendments, Schedule A sections)"]
    SEARCH --> LINKS
    LINKS --> WEAK{"Clause id given, or best score ≥ 3.0<br/>with ≥ 2 question words matched?"}
    WEAK -->|no| NF["NOT IN DOCUMENTS<br/>no LLM call"]
    WEAK -->|yes| MODE{"Provider"}
    MODE -->|extractive| QUOTE["Quote top clause +<br/>its amendment / Schedule A link"]
    MODE -->|"claude / openai / gemini"| LLM["LLM answers from the<br/>retrieved clauses only"]
    LLM -->|call fails| QUOTE
    LLM --> CITE["Every cited id must be<br/>among retrieved clauses → else ⚠"]
    QUOTE --> CITE

    ASK --> OUT
    ENGINE --> OUT
    NF --> OUT
    CITE --> OUT(["Answer block: Answer · Clause references ·<br/>Warnings · Answered by"])
    OUT --> SAVE["Save turn to memory (last 5)"]
```

### 3. Retrieval

```mermaid
flowchart LR
    Q["Question"] --> TOK["Tokenise<br/>lowercase · drop stop words ·<br/>'cities'→'city', 'invoices'→'invoice'"]
    TOK --> DROP["Remove the merchant's own<br/>name/id words<br/>(the filter handles those)"]
    DROP --> SYN["Add synonyms<br/>'payment' → 'payable'<br/>'notice' → 'terminate'"]
    SYN --> POOL["Candidate pool<br/>named merchant's clauses<br/>+ Schedule A<br/>(all clauses if none named)"]
    POOL --> SCORE["BM25 score per clause<br/>× 1.25 for the named<br/>merchant's own clauses"]
    SCORE --> TOP["Top 5"]
    ID["Clause id in question<br/>e.g. M04-§6.1"] --> EXACT["Exact match<br/>(always kept)"]
    TOP --> EXP["Expand links<br/>(only for merchants in scope)"]
    EXACT --> EXP
    EXP --> HITS["Retrieved clauses<br/>with score · matched words · how found<br/>(shown by /sources)"]
```

### 4. Amendments travel with the clause they change

Links are found by scanning each clause for clause ids and "section A.x", in both directions, and clauses of
the same annexure are linked to each other. Retrieving any one of them brings the rest.

```mermaid
flowchart LR
    subgraph M09["Lakdi Furniture (M09)"]
        A1["M09-§2.1<br/>discount 8%"]
        B1["M09-Ann1.1<br/>12% from 1 Sep 2026"]
        C1["M09-Ann1.2<br/>other terms unchanged"]
        A1 <-->|"amended by"| B1
        B1 <-->|"same annexure"| C1
    end
    subgraph M02["Tara Spices (M02)"]
        A2["M02-§3.1<br/>COD: max ₹25 or 1.5%"]
        B2["M02-Ann1.1<br/>no COD fee 1–31 Aug 2026"]
        C2["M02-Ann1.2<br/>§3.1 applies again from 1 Sep"]
        A2 <-->|"referenced by"| C2
        B2 <-->|"same annexure"| C2
    end
    A2 <-->|"in place of"| SA4["A.4<br/>Schedule A COD fee"]
```

In calculations the dates are applied in code (`contracts.py` amendments with start/end dates), not by the LLM.

### 5. One question, end to end (LLM mode)

```mermaid
sequenceDiagram
    actor U as User
    participant T as Terminal UI
    participant A as Assistant
    participant R as Retriever
    participant L as LLM provider
    U->>T: What freight discount applies to a<br/>Lakdi Furniture shipment on 5 Sep 2026?
    T->>A: ask(question)
    A->>A: memory: not a follow-up · merchant = M09 · not a calculation
    A->>R: search(question, merchants=[M09])
    R-->>A: M09-Ann1.1 (top score), M09-§2.1, A.5, … + linked clauses
    A->>A: best match strong enough → continue
    A->>L: system rules + retrieved clauses + question (+ last 5 turns)
    alt provider answers
        L-->>A: "12% … [M09-Ann1.1] … replaces 8% in [M09-§2.1]"
    else quota / overload / timeout
        L-->>A: error
        A->>A: quote the clauses instead, add ⚠ warning
    end
    A->>A: citation check: every id was retrieved?
    A-->>T: Answer · Clause references · Warnings · Answered by
    T-->>U: structured answer block
    A->>A: save turn to memory
```

### 6. Choosing a provider, and Gemini's model fallback

```mermaid
flowchart TD
    START(["Start"]) --> FORCED{"--provider or<br/>RAG_PROVIDER set?"}
    FORCED -->|yes| USE["Use it<br/>(error if its key is missing)"]
    FORCED -->|no| K1{"ANTHROPIC_API_KEY?"}
    K1 -->|yes| CLAUDE["Claude · claude-opus-5-5<br/>server-side refusal fallback"]
    K1 -->|no| K2{"OPENAI_API_KEY?"}
    K2 -->|yes| OPENAI["OpenAI · gpt-4.1"]
    K2 -->|no| K3{"GEMINI_API_KEY /<br/>GOOGLE_API_KEY?"}
    K3 -->|yes| GEM["Gemini"]
    K3 -->|no| EXT["Extractive (offline)"]

    GEM --> G1["gemini-3.5-flash"]
    G1 -->|"429 quota used up"| G2["gemini-3.6-flash"]
    G2 -->|429| G3["gemini-3-flash-preview"]
    G3 -->|429| G4["gemini-3.5-flash-lite"]
    G4 -->|429| G5["gemini-3.1-flash-lite"]
    G5 -->|"all fail"| EXT2["Quote clauses for this question<br/>+ ⚠ warning"]
    G1 -.->|"503 overloaded:<br/>wait 10 s, 20 s, then next model"| G2
```

Each Gemini call has a 30 s timeout and the SDK's own retries are off, so a call never hangs. A key that exists but
has no credits still counts as available (see Limitations).

## How it's built

### Pieces

| File | Role |
|---|---|
| `clauses.py` | Splits the markdown into 215 citable chunks: Schedule A sections (`A.1`–`A.5`), agreement preambles (`M01 preamble`), numbered clauses (`M01-§4.2`), annexures (`M09-Ann1.1`). Links clauses that refer to each other, and annexure clauses to each other. |
| `retrieve.py` | BM25 keyword ranking, merchant detection (by id or name), a small synonym list (e.g. "payment terms" → "payable"), a 1.25× boost for the named merchant's own clauses (they override Schedule A), exact clause-id lookup, linked-clause expansion. |
| `calculate.py` | Reads merchant, date, weight, dimensions, payment mode, COD value and destination from the question; computes with the billing engine; cites the clause for each step; asks for anything missing. |
| `llm.py` | Provider selection, the system prompt, and the Claude / OpenAI / Gemini calls. Loads `.env`. |
| `assistant.py` | The flow above, plus 5-turn memory and citation checking. |
| `__main__.py` | Terminal UI and batch mode. |

### Design choices

- **Clause-level chunks.** The unit of retrieval is the unit of citation, so every answer can point to an exact clause id.
- **BM25, not embeddings.** The contracts are short and use consistent wording; keyword ranking is fast, needs no model download, and `/sources` shows exactly why a clause ranked first.
- **Amendments travel with the clause they change.** Asking about M09's discount always brings `M09-Ann1.1` (12% from 1 Sep 2026) alongside `M09-§2.1` (8%), so the date-dependent answer is never missed.
- **Numbers come from code, not the LLM.** Calculation questions bypass the LLM and use the same billing engine as the Part 1 audit (the same engine 56 unit tests cover).
- **Saying "not in the documents".** Two layers: if the best clause scores below 3.0 (BM25) or matches fewer than 2 question words, the assistant answers `NOT IN DOCUMENTS` without calling an LLM; otherwise the LLM is instructed to reply `NOT IN DOCUMENTS: ...` when the provided clauses don't answer the question.
- **Citation check.** Any clause id the LLM cites that wasn't retrieved is flagged with a ⚠ line under the answer.

## Two modes: LLM and extractive

| Mode | When | What you get |
|---|---|---|
| **LLM** | an API key is found (or `--provider claude/openai/gemini`) | The LLM writes a short answer from the retrieved clauses only, citing clause ids. |
| **Extractive** | no API key, or forced with `--provider extractive` / `RAG_PROVIDER=extractive` / `/provider extractive` | No LLM: the best-matching clause is quoted verbatim, plus any amendment or Schedule A section linked to it. Works fully offline. |

**Automatic fallback:** in LLM mode, if the call fails for any reason (quota, billing, overload, network, timeout),
that question is still answered in extractive mode, with a ⚠ warning saying why.

In both modes, calculation questions go to the billing engine and weak matches get `NOT IN DOCUMENTS` without an LLM,
so those answers are identical either way.

## Providers and configuration

The first provider with a key wins: **Claude → OpenAI → Gemini → extractive**. Override with `--provider` or `RAG_PROVIDER`.

| Provider | Key (env var or `.env`) | Default model | Override |
|---|---|---|---|
| Claude | `ANTHROPIC_API_KEY` | `claude-opus-5-5` | `RAG_CLAUDE_MODEL` |
| OpenAI | `OPENAI_API_KEY` | `gpt-4.1` | `RAG_OPENAI_MODEL` |
| Gemini | `GEMINI_API_KEY` or `GOOGLE_API_KEY` | `gemini-3.5-flash` | `RAG_GEMINI_MODEL` |
| Extractive | — | quotes clauses | — |

- **Gemini free tier:** each model has its own quota. If the main model's quota is used up (429) the assistant moves straight to the next model (`gemini-3.6-flash` → `gemini-3-flash-preview` → `gemini-3.5-flash-lite` → `gemini-3.1-flash-lite`; override with a comma-separated `RAG_GEMINI_FALLBACK_MODELS`). If a model is overloaded (503) it waits 10 s, then 20 s, before moving on. Each call times out after 30 s. If every model fails, the answer falls back to quoting the clauses, with a ⚠ warning.
- **Claude:** requests use server-side refusal fallback (`fallbacks: "default"`), so a safety refusal is retried on a fallback model automatically.
- Temperature 0 (OpenAI/Gemini) for repeatable answers.

## Tests

```bash
pytest tests/test_assistant.py -q
```

Offline tests (the LLM is stubbed): clause parsing, amendment links, merchant detection, ranking, clause-id lookup,
"not in documents", follow-up memory, the 5-turn limit, calculations (M02 waiver boundaries, M09 September discount,
missing inputs) and the unretrieved-citation warning.

## Limitations

- Calculation parsing uses simple patterns: give a merchant, date, weight (`1.2 kg`), dimensions (`30x20x10`),
  `COD ₹2,000` or `prepaid`, and `to <city>`. Missing inputs are asked for, not guessed.
- Delivery-city states come from the cities in `shipments.csv` plus the pickup cities; other cities need their state.
- **"Available" means a key is set, not that it works.** The first provider with a key is used even if that account
  has no credits or quota. Every question then tries that provider, fails, and drops to extractive; the next provider
  (e.g. Gemini after OpenAI) is not tried. Workaround: set `RAG_PROVIDER=gemini` in `.env`, start with
  `--provider gemini`, or use `/provider gemini`. A possible improvement: on a quota or billing error, try the next
  provider that has a key, and skip the failed one for the rest of the session.
- **Extractive answers are narrow.** They quote the top clause and what links to it. For "which merchant..."
  questions that is one merchant's clause, not all twelve. A possible improvement: show the top two or three clauses
  with their titles, and one clause per merchant for comparison questions (retrieval already finds them; only the
  extractive answer drops them).
- BM25 matches words, not meaning: an unusual paraphrase can miss a clause. `/sources` shows what was retrieved, and
  the synonym list in `retrieve.py` is where to fix it.
