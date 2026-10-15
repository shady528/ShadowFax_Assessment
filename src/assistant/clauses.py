"""Split data/contracts_and_rate_card.md into citable clauses.

Each chunk is one thing you can cite:
  Schedule A sections      -> "A.1" ... "A.5" (plus "Schedule A" intro)
  Agreement preambles      -> "M01 preamble" (parties, effective date, pickup city)
  Numbered clauses         -> "M01-§4.2"
  Annexure (amendment)     -> "M09-Ann1.1"
"""
import re
from dataclasses import dataclass, field
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
CONTRACTS_PATH = ROOT / "data" / "contracts_and_rate_card.md"

CLAUSE_LINE = re.compile(r"^(M\d{2}-(?:§\d+\.\d+|Ann\d+\.\d+))\s+(.*)$")
CLAUSE_REF = re.compile(r"\b(M\d{2}-(?:§\d+\.\d+|Ann\d+\.\d+))|section\s+(A\.\d)\b|\b(A\.\d)\b")
MERCHANT_HEADING = re.compile(r"^# Merchant Service Agreement — (.+) \((M\d{2})\)$")


@dataclass
class Clause:
    id: str                       # what gets cited, e.g. "M01-§4.2"
    text: str                     # clause text as written
    section: str                  # section title, e.g. "Invoicing and payment"
    merchant_id: str = ""         # "" for Schedule A
    merchant_name: str = ""
    links: list[str] = field(default_factory=list)   # clause ids this one refers to / is amended by

    @property
    def search_text(self) -> str:
        """Text used for retrieval: clause + section title + merchant name/id."""
        return f"{self.id} {self.merchant_id} {self.merchant_name} {self.section} {self.text}"


def parse_contracts(path: Path = CONTRACTS_PATH) -> list[Clause]:
    clauses: list[Clause] = []
    merchant_id = merchant_name = section = ""
    in_schedule_a = False
    buffer: list[str] = []          # lines of the current Schedule A section / preamble
    buffer_id = ""

    def flush():
        nonlocal buffer, buffer_id
        text = "\n".join(l for l in buffer if l.strip()).strip()
        if buffer_id and text:
            clauses.append(Clause(buffer_id, text, section, merchant_id, merchant_name))
        buffer, buffer_id = [], ""

    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.rstrip()
        if line.startswith("# Schedule A"):
            flush()
            in_schedule_a, section, buffer_id = True, "Schedule A — Standard Rate Card and Charging Rules", "Schedule A"
            continue
        m = MERCHANT_HEADING.match(line)
        if m:
            flush()
            in_schedule_a = False
            merchant_name, merchant_id = m.group(1), m.group(2)
            section, buffer_id = "Agreement preamble", f"{merchant_id} preamble"
            continue
        if line.startswith("## "):
            flush()
            heading = line[3:].strip()
            if in_schedule_a:                                  # "## A.4 Cash-on-delivery (COD) fee"
                buffer_id, section = heading.split(" ", 1)[0], heading.split(" ", 1)[1]
            else:                                              # "## M01-§4 Invoicing and payment" / "## M02 Annexure 1 — ..."
                section = heading.split(" ", 1)[1] if "§" in heading.split(" ", 1)[0] else heading
            continue
        if line.strip() == "---":
            flush()
            continue
        m = CLAUSE_LINE.match(line)
        if m:
            flush()
            clauses.append(Clause(m.group(1), m.group(2), section, merchant_id, merchant_name))
            continue
        if buffer_id:
            buffer.append(line)
    flush()
    _link(clauses)
    return clauses


def _link(clauses: list[Clause]) -> None:
    """Connect clauses that refer to each other (both directions).

    e.g. M09-Ann1.1 amends M09-§2.1, and M01-§3.1 points to Schedule A section A.4.
    Retrieval uses these links to pull in an amendment whenever the clause it
    changes is retrieved.
    """
    by_id = {c.id: c for c in clauses}

    def connect(a: Clause, b: Clause) -> None:
        if b.id not in a.links:
            a.links.append(b.id)
        if a.id not in b.links:
            b.links.append(a.id)

    # clauses of the same annexure belong together (Ann1.1 <-> Ann1.2)
    for a in clauses:
        for b in clauses:
            if a is not b and "-Ann" in a.id and a.id.rsplit(".", 1)[0] == b.id.rsplit(".", 1)[0]:
                connect(a, b)

    for c in clauses:
        for match in CLAUSE_REF.finditer(c.text):
            ref = next(g for g in match.groups() if g)
            if ref in by_id and ref != c.id:
                connect(c, by_id[ref])
