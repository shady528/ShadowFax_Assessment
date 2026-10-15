"""Contract assistant: parsing, retrieval, not-found, calculations, memory, citation checks (offline)."""
import pytest

from src.assistant import llm
from src.assistant.assistant import NOT_FOUND, ContractAssistant
from src.assistant.calculate import calculate, is_calculation, parse_request
from src.assistant.clauses import parse_contracts
from src.assistant.retrieve import Retriever, tokenize


@pytest.fixture(scope="module")
def retriever():
    return Retriever()


@pytest.fixture
def bot(retriever, monkeypatch):
    for v in ["ANTHROPIC_API_KEY", "ANTHROPIC_AUTH_TOKEN", "OPENAI_API_KEY", "GEMINI_API_KEY", "GOOGLE_API_KEY", "RAG_PROVIDER"]:
        monkeypatch.delenv(v, raising=False)
    return ContractAssistant(retriever=retriever)


def test_every_clause_parsed():
    ids = {c.id for c in parse_contracts()}
    assert {"A.1", "A.2", "A.3", "A.4", "A.5", "M01 preamble", "M01-§4.2", "M12-§8.2", "M02-Ann1.2", "M09-Ann1.1"} <= ids
    assert sum(1 for i in ids if i.endswith("preamble")) == 12


def test_amendments_linked_to_clause_they_change():
    by = {c.id: c for c in parse_contracts()}
    assert "M09-Ann1.1" in by["M09-§2.1"].links
    assert "M02-Ann1.1" in by["M02-Ann1.2"].links and "M02-Ann1.2" in by["M02-§3.1"].links


def test_tokenize():
    assert tokenize("M01's invoices in metro cities") == ["m01", "invoice", "metro", "city"]


def test_merchant_detected_by_name_or_id(retriever):
    assert retriever.merchants_in("payment terms for Chandan Home and m05") == ["M01", "M05"]


def test_merchant_clause_ranks_first(retriever):
    hits = retriever.search("What is the minimum chargeable weight for Patang Apparel?")
    assert hits[0].clause.id == "M07-§2.3"


def test_clause_id_lookup(retriever):
    hits = retriever.search("What does M04-§6.1 say?")
    assert hits[0].clause.id == "M04-§6.1" and hits[0].via == "clause id"


def test_not_in_documents(bot):
    ans = bot.ask("What is the fuel surcharge?")
    assert ans.kind == "not found" and ans.text.startswith(NOT_FOUND)


def test_extractive_answer_cites_clause(bot):
    ans = bot.ask("What are the payment terms for Chandan Home?")
    assert ans.citations[0] == "M01-§4.2" and "fifteen (15) days" in ans.text


def test_follow_up_uses_memory(bot):
    bot.ask("What are the payment terms for Chandan Home?")
    ans = bot.ask("and for M05?")
    assert ans.citations[0] == "M05-§4.2"


def test_memory_keeps_last_five(bot):
    for i in range(7):
        bot.ask(f"What are the payment terms for M0{i % 9 + 1}?")
    assert len(bot.memory) == 5


def test_calculation_cod_waiver_boundaries():
    q = "COD fee for Tara Spices on a ₹2,000 COD order on {}?"
    assert is_calculation(q.format("31 Aug"))
    aug, _ = calculate(parse_request(q.format("31 Aug"), "M02"))
    sep, _ = calculate(parse_request(q.format("1 Sep"), "M02"))
    assert "₹0.00" in aug[0] and "M02-Ann1.1" in aug[0]
    assert "₹30.00" in sep[0] and "M02-§3.1" in sep[0]


def test_calculation_full_charge_m09_september():
    lines, cited = calculate(parse_request(
        "Charge for Lakdi Furniture, 1.2 kg prepaid 30x20x10 to Delhi on 2026-09-05", "M09"))
    assert "Zone D" in lines[1] and "₹95.04" in lines[-1] and "M09-Ann1.1" in cited


def test_calculation_asks_for_missing_input():
    lines, _ = calculate(parse_request("What's the freight for Moti Jewels for 2 kg on 3 Aug?", "M04"))
    assert any("delivery city" in l for l in lines)


def test_unretrieved_citation_is_flagged(bot, monkeypatch):
    bot.provider = "claude"
    monkeypatch.setattr(llm, "generate", lambda *a: ("Payable in 15 days [M01-§4.2], see also [M07-§9.9].", "stub-model"))
    ans = bot.ask("What are the payment terms for Chandan Home?")
    assert ans.citations == ["M01-§4.2", "M07-§9.9"]
    assert ans.warnings == ["cited M07-§9.9 but it was not among the retrieved clauses"]
