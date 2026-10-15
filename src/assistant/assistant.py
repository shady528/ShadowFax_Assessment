"""ContractAssistant: retrieval + answer + short memory.

Flow for each question:
  1. Resolve follow-ups with memory ("and for M03?" reuses the last question's topic/merchant).
  2. Calculation question with a merchant -> billing engine (numbers never come from an LLM).
  3. Otherwise retrieve clauses. Too weak a match -> "not in the documents" without calling an LLM.
  4. Write the answer with the chosen provider (or quote clauses in extractive mode).
  5. Check every cited clause id was actually retrieved; flag any that wasn't.
"""
import re
from collections import deque
from dataclasses import dataclass, field

from src.assistant import llm
from src.assistant.calculate import calculate, is_calculation, parse_request
from src.assistant.retrieve import CLAUSE_ID, Hit, Retriever

MEMORY_TURNS = 5
MIN_SCORE = 3.0                 # best BM25 score below this = the documents don't cover the question
MIN_MATCHED_TERMS = 2           # ...or fewer distinct question words matched in the best clause
NOT_FOUND = "NOT IN DOCUMENTS"
FOLLOW_UP = re.compile(r"^\s*(and|what about|how about|same for|also|for)\b|\b(they|their|them|its|that one|this one)\b", re.I)
COMPARISON = re.compile(r"\b(which|what|all|each|every|any) merchants?\b|\bhighest\b|\blowest\b|\bmost\b|\bleast\b|\bcompare\b", re.I)
QUESTION_NUMBER = re.compile(r"^\s*Q?\d+\s*[.:)\-]\s*", re.I)    # "Q1. ..." / "3) ..." typed before a question
CITATION = re.compile(r"\[([^\]]+)\]")


@dataclass
class Answer:
    question: str
    text: str
    citations: list[str]
    kind: str                       # "calculation" | "retrieval" | "not found"
    provider: str
    hits: list[Hit] = field(default_factory=list)
    retrieval_query: str = ""
    warnings: list[str] = field(default_factory=list)
    model: str = ""                 # model that actually answered (a fallback model may have been used)


@dataclass
class Turn:
    question: str
    retrieval_query: str
    merchants: list[str]
    answer: str


class ContractAssistant:
    def __init__(self, provider: str | None = None, retriever: Retriever | None = None):
        self.retriever = retriever or Retriever()
        self.provider = llm.choose_provider(provider)
        self.memory: deque[Turn] = deque(maxlen=MEMORY_TURNS)
        self.last: Answer | None = None

    # --- memory --------------------------------------------------------------

    def _resolve(self, question: str) -> tuple[str, list[str]]:
        """Retrieval query + merchants, using the previous turn for follow-ups."""
        merchants = self.retriever.merchants_in(question)
        if not self.memory:
            return question, merchants
        prev = self.memory[-1]
        own_terms = self.retriever.query_terms(question, merchants)
        is_follow_up = bool(FOLLOW_UP.search(question)) or len(own_terms) < 2
        if not is_follow_up:
            return question, merchants
        query = f"{question} {prev.retrieval_query}" if len(own_terms) < 2 else question
        return query, merchants or prev.merchants

    def clear(self) -> None:
        self.memory.clear()

    # --- answering -----------------------------------------------------------

    def ask(self, question: str) -> Answer:
        question = QUESTION_NUMBER.sub("", question).strip()
        query, merchants = self._resolve(question)
        if len(merchants) == 1 and is_calculation(question):
            answer = self._calculate(question, query, merchants[0])
        else:
            answer = self._retrieve_and_answer(question, query, merchants)
        self.memory.append(Turn(question, query, merchants, answer.text))
        self.last = answer
        return answer

    def _calculate(self, question: str, query: str, merchant_id: str) -> Answer:
        lines, cited = calculate(parse_request(question, merchant_id))
        hits = [Hit(self.retriever.by_id[c], 0.0, [], "calculation") for c in cited if c in self.retriever.by_id]
        text = "\n".join(lines) + "\n(Computed with the billing engine from the contract terms.)"
        return Answer(question, text, cited, "calculation", "billing engine", hits, query)

    def _retrieve_and_answer(self, question: str, query: str, merchants: list[str]) -> Answer:
        across = not merchants and bool(COMPARISON.search(question))
        hits = self.retriever.search_each_merchant(query) if across else self.retriever.search(query, merchants=merchants)
        ranked = sorted((h for h in hits if h.via == "bm25"), key=lambda h: h.score, reverse=True)
        exact = [h for h in hits if h.via == "clause id"]
        best = ranked[0] if ranked else None
        weak = best is None or best.score < MIN_SCORE or len(best.matched) < MIN_MATCHED_TERMS
        if weak and not exact:
            text = (f"{NOT_FOUND}: the contracts and Schedule A don't contain this. "
                    f"Closest clause: {best.clause.id} (weak match)." if best else
                    f"{NOT_FOUND}: nothing in the contracts or Schedule A matches this question.")
            return Answer(question, text, [], "not found", "retrieval threshold", hits, query)

        provider, model, warnings = self.provider, "", []
        if provider != "extractive":
            history = [m for t in self.memory for m in ({"role": "user", "content": t.question},
                                                       {"role": "assistant", "content": t.answer})]
            try:
                text, model = llm.generate(provider, history, self._prompt(question, hits))
                cited = [c.strip() for group in CITATION.findall(text) for c in group.split(",")]
            except Exception as e:                      # provider down / quota: still answer, from the clauses
                warnings.append(f"{provider} failed ({type(e).__name__}: {str(e)[:80]}); quoted the clauses instead")
                provider = "extractive"
        if provider == "extractive":
            text, cited = self._extractive(hits)
        kind = "not found" if text.startswith(NOT_FOUND) else "retrieval"
        answer = Answer(question, text, list(dict.fromkeys(cited)), kind, provider, hits, query, model=model)
        retrieved = {h.clause.id for h in hits}
        answer.warnings = warnings + [f"cited {c} but it was not among the retrieved clauses"
                                      for c in answer.citations if c not in retrieved]
        return answer

    def _prompt(self, question: str, hits: list[Hit]) -> str:
        blocks = []
        for h in hits:
            c = h.clause
            who = f"{c.merchant_name} ({c.merchant_id})" if c.merchant_id else "Schedule A"
            blocks.append(f"[{c.id}] {who} — {c.section}\n{c.text}")
        return "Clauses:\n\n" + "\n\n".join(blocks) + f"\n\nQuestion: {question}"

    def _extractive(self, hits: list[Hit]) -> tuple[str, list[str]]:
        """No LLM: quote the best clause, plus any amendment/related clause linked to it."""
        ranked = [h for h in hits if h.via in ("bm25", "clause id")]
        top = ranked[0]
        chosen = [top] + [h for h in hits if h.via == f"linked from {top.clause.id}"
                          and ("Ann" in h.clause.id or h.clause.id.startswith("A."))]
        lines = [f"[{h.clause.id}] {h.clause.text}" for h in chosen]
        return "\n".join(lines), [h.clause.id for h in chosen]
