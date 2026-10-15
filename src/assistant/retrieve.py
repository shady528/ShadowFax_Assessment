"""Retrieval: BM25 keyword ranking over clauses, with merchant filtering,
exact clause-ID lookup, and linked clauses (amendments, Schedule A sections).

Plain BM25 so every ranking can be explained: a clause scores higher when it
contains more of the question's (rarer) words.
"""
import math
import re
from collections import Counter
from dataclasses import dataclass

from src.assistant.clauses import Clause, parse_contracts

STOPWORDS = set("""a an the of for to in on at by with and or is are was were be been being what which who whom
whose when where how does do did can could would should will shall may might must it its this that these those
there their they them his her he she we our you your i me my under per any each from as into than then about
please tell give show me much many""".split())

# a few contract words the questions may phrase differently (both directions are searched)
SYNONYMS = {
    "payment": ["payable"], "pay": ["payable"], "due": ["payable"],
    "late": ["overdue", "interest"], "penalty": ["interest", "overdue"],
    "notice": ["terminate", "termination"], "cancel": ["terminate"], "exit": ["terminate"],
    "liability": ["liability", "lost", "damaged"], "lost": ["liability"], "damage": ["damaged"],
    "cap": ["limited"], "compensation": ["liability"],
    "dispute": ["dispute", "disputes"], "window": ["within", "days"],
    "cod": ["cash", "delivery", "cod"], "fee": ["fee"],
    "discount": ["discount"], "divisor": ["divisor", "volumetric"],
    "minimum": ["minimum"], "weight": ["weight", "chargeable"],
    "zone": ["zone", "zones"], "metro": ["metro"], "rate": ["rate", "card"],
    "remit": ["remitted"], "remittance": ["remitted"], "settle": ["remitted", "settled"],
    "invoice": ["invoice", "invoices"], "billing": ["invoice"], "jurisdiction": ["courts", "governed"],
    "law": ["governed", "law"], "renew": ["renew"], "renewal": ["renew", "runs"], "duration": ["runs", "renew"],
    "warehouse": ["pickups", "warehouse"], "pickup": ["pickups"], "start": ["effective"],
}

MERCHANT_BOOST = 1.25      # a merchant's own clause outranks Schedule A ("unless the agreement states otherwise")

MERCHANT_ID = re.compile(r"\bM(0[1-9]|1[0-2])\b", re.IGNORECASE)
CLAUSE_ID = re.compile(r"\b(M\d{2}-(?:§\d+\.\d+|Ann\d+\.\d+)|A\.\d)\b")


def tokenize(text: str) -> list[str]:
    text = re.sub(r"['’]s\b", "", text.lower()).replace("§", " ")     # "m01's" -> "m01"
    out = []
    for w in re.findall(r"[a-z0-9]+(?:\.[0-9]+)?", text):
        if w in STOPWORDS:
            continue
        if len(w) > 4 and w.endswith("ies"):
            w = w[:-3] + "y"                   # "cities" -> "city"
        elif len(w) > 4 and w.endswith("s") and not w.endswith("ss"):
            w = w[:-1]                         # "invoices" -> "invoice"
        out.append(w)
    return out


@dataclass
class Hit:
    clause: Clause
    score: float
    matched: list[str]          # query words found in the clause
    via: str = "bm25"           # "bm25" | "clause id" | "linked from <id>"


class Retriever:
    def __init__(self, clauses: list[Clause] | None = None, k1: float = 1.5, b: float = 0.75):
        self.clauses = clauses or parse_contracts()
        self.by_id = {c.id: c for c in self.clauses}
        self.docs = [tokenize(c.search_text) for c in self.clauses]
        self.tf = [Counter(d) for d in self.docs]
        self.avg_len = sum(len(d) for d in self.docs) / len(self.docs)
        n = len(self.docs)
        df = Counter(w for d in self.docs for w in set(d))
        self.idf = {w: math.log(1 + (n - f + 0.5) / (f + 0.5)) for w, f in df.items()}
        self.k1, self.b = k1, b
        self.merchants = {c.merchant_id: c.merchant_name for c in self.clauses if c.merchant_id}

    # --- what the question is about -----------------------------------------

    def merchants_in(self, text: str) -> list[str]:
        """Merchant ids mentioned by id (M01) or by name (full name or its first word)."""
        found = {f"M{m.group(1)}" for m in MERCHANT_ID.finditer(text)}
        low = text.lower()
        for mid, name in self.merchants.items():
            if name.lower() in low or re.search(rf"\b{re.escape(name.split()[0].lower())}\b", low):
                found.add(mid)
        return sorted(found)

    def query_terms(self, question: str, merchants: list[str] = ()) -> list[str]:
        """Question words + synonyms, minus the merchant's own name/id (the merchant filter handles those)."""
        drop = set()
        for mid in merchants:
            drop |= set(tokenize(f"{mid} {self.merchants[mid]}"))
        terms = [t for t in tokenize(question) if t not in drop]
        expanded = list(terms)
        for t in terms:
            expanded += [w for w in SYNONYMS.get(t, []) if w not in expanded]
        return expanded

    # --- ranking -------------------------------------------------------------

    def _bm25(self, i: int, terms: list[str]) -> tuple[float, list[str]]:
        score, matched, length = 0.0, [], len(self.docs[i])
        for t in set(terms):
            f = self.tf[i].get(t, 0)
            if not f:
                continue
            matched.append(t)
            score += self.idf.get(t, 0) * f * (self.k1 + 1) / (f + self.k1 * (1 - self.b + self.b * length / self.avg_len))
        return score, matched

    def search(self, question: str, k: int = 5, merchants: list[str] | None = None) -> list[Hit]:
        """Top-k clauses for the question, plus clause-id hits and linked clauses."""
        merchants = merchants if merchants is not None else self.merchants_in(question)
        terms = self.query_terms(question, merchants)

        hits: dict[str, Hit] = {}
        for cid in CLAUSE_ID.findall(question):               # exact clause id in the question
            if cid in self.by_id:
                hits[cid] = Hit(self.by_id[cid], float("inf"), [], "clause id")

        pool = [i for i, c in enumerate(self.clauses)
                if not merchants or c.merchant_id in merchants or not c.merchant_id]
        scored = []
        for i in pool:
            score, matched = self._bm25(i, terms)
            if merchants and self.clauses[i].merchant_id in merchants:
                score *= MERCHANT_BOOST
            scored.append((score, matched, i))
        scored.sort(key=lambda t: t[0], reverse=True)
        for score, matched, i in scored[:k]:
            if score > 0 and self.clauses[i].id not in hits:
                hits[self.clauses[i].id] = Hit(self.clauses[i], score, sorted(matched))

        self._add_links(hits, merchants)
        return list(hits.values())

    def search_each_merchant(self, question: str, schedule_a: int = 2) -> list[Hit]:
        """For "which merchant..." questions: each merchant's best clause (+ its amendments) and the top Schedule A sections."""
        terms = self.query_terms(question)
        hits: dict[str, Hit] = {}
        groups = {mid: [i for i, c in enumerate(self.clauses) if c.merchant_id == mid] for mid in sorted(self.merchants)}
        groups[""] = [i for i, c in enumerate(self.clauses) if not c.merchant_id]
        for mid, idx in groups.items():
            ranked = sorted(((*self._bm25(i, terms), i) for i in idx), key=lambda t: t[0], reverse=True)
            for score, matched, i in ranked[: schedule_a if mid == "" else 1]:
                if score > 0:
                    hits[self.clauses[i].id] = Hit(self.clauses[i], score, sorted(matched))
        self._add_links(hits, sorted(self.merchants))
        return sorted(hits.values(), key=lambda h: h.score, reverse=True)

    def _add_links(self, hits: dict[str, Hit], merchants: list[str]) -> None:
        """Pull in linked clauses (amendments, referenced Schedule A sections) for the merchants in scope."""
        for hit in list(hits.values()):
            for lid in hit.clause.links:
                linked = self.by_id[lid]
                in_scope = not linked.merchant_id or linked.merchant_id == hit.clause.merchant_id \
                    or linked.merchant_id in merchants
                if lid not in hits and in_scope:
                    hits[lid] = Hit(linked, 0.0, [], f"linked from {hit.clause.id}")
