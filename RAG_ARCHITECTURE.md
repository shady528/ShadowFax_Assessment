# Contract assistant — RAG architecture

```mermaid
flowchart LR
    DOCS[("Contracts +<br/>Schedule A")] --> CHUNK["Split into<br/>215 clauses"]
    CHUNK --> INDEX["BM25 index<br/>+ amendment links"]

    Q(["Question"]) --> ROUTE{"Calculation?"}
    ROUTE -->|yes| ENGINE["Billing engine<br/>(same as Part 1)"]
    ROUTE -->|no| RETRIEVE["Retrieve clauses<br/>(merchant filter + linked amendments)"]
    INDEX --> RETRIEVE
    RETRIEVE --> MATCH{"Good match?"}
    MATCH -->|no| NF["Not in the documents"]
    MATCH -->|yes| LLM["LLM answers from<br/>those clauses only<br/>(Claude / OpenAI / Gemini,<br/>else quote clauses)"]

    ENGINE --> A(["Answer with<br/>clause references"])
    LLM --> A
    NF --> A
```

1. **Index:** the contracts are split into one chunk per citable clause (e.g. `M01-§4.2`, `A.4`, `M09-Ann1.1`); each amendment is linked to the clause it changes.
2. **Route:** questions that need a number (e.g. a COD fee on ₹3,000) go to the billing engine, so numbers never come from the LLM.
3. **Retrieve:** keyword (BM25) search over the clauses of the merchant named, plus Schedule A; amendments come along with the clause they amend.
4. **Answer:** if nothing matches well, it says the documents don't contain the answer. Otherwise the LLM answers only from the retrieved clauses and cites them; without an API key it quotes the clauses instead.

Detailed diagrams: [`src/assistant/README.md`](src/assistant/README.md#architecture).
