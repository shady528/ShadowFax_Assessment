"""Terminal contract assistant.

    python -m src.assistant                      # interactive
    python -m src.assistant --provider openai    # force a provider
    python -m src.assistant --questions q.txt    # answer one question per line, write outputs/contract_answers.md
"""
import argparse
import shutil
import textwrap
from pathlib import Path

from src.assistant import llm
from src.assistant.assistant import MEMORY_TURNS, Answer, ContractAssistant

ROOT = Path(__file__).resolve().parents[2]
HELP = f"""Commands:
  /help              show this help
  /sources           clauses retrieved for the last answer (with scores)
  /history           last {MEMORY_TURNS} questions kept in memory
  /clear             forget the conversation
  /provider [name]   show or switch provider ({', '.join(llm.PROVIDERS)})
  /exit, /quit       leave (Ctrl-D also works)"""


def banner(a: ContractAssistant) -> str:
    keys = ", ".join(p for p in llm.PROVIDERS[:-1] if llm.available(p)) or "none"
    return (f"Shadowfax contract assistant — {len(a.retriever.clauses)} clauses from Schedule A + 12 agreements\n"
            f"Provider: {a.provider} (model: {llm.model_for(a.provider)}) | API keys found: {keys}\n"
            f"Ask a question, or /help. Memory: last {MEMORY_TURNS} turns.")


def _width() -> int:
    return min(shutil.get_terminal_size((100, 20)).columns, 100)


def _clause_title(a: ContractAssistant, cid: str) -> str:
    """'Shilp Decor (M11) · Freight charges' / 'Schedule A · Chargeable weight'."""
    c = a.retriever.by_id.get(cid)
    if c is None:
        return "not found in the contracts"
    who = f"{c.merchant_name} ({c.merchant_id})" if c.merchant_id else "Schedule A"
    return f"{who} · {c.section}"


def _answered_by(ans: Answer) -> str:
    if ans.kind == "calculation":
        return "billing engine (src/billing.py), no LLM"
    if ans.provider == "retrieval threshold":
        return "retrieval only — no clause matched well enough, no LLM called"
    if ans.provider == "extractive":
        return "extractive (quoted clauses, no LLM)"
    return f"{ans.provider} ({ans.model})"


def render(a: ContractAssistant, ans: Answer) -> str:
    w = _width()
    rule = "─" * w
    wrap = lambda t: "\n".join(textwrap.fill(p, w - 2, initial_indent="  ", subsequent_indent="  ")
                              for p in t.splitlines() if p.strip())
    out = [rule, textwrap.fill(f"Q: {ans.question}", w), rule, "Answer", wrap(ans.text)]
    if ans.citations:
        pad = max(len(c) for c in ans.citations) + 2
        out += ["", "Clause references"]
        out += [f"  {('[' + c + ']').ljust(pad + 1)} {_clause_title(a, c)}" for c in ans.citations]
    if ans.warnings:
        out += ["", "Warnings"] + [f"  ⚠ {w_}" for w_ in ans.warnings]
    out += ["", f"Answered by: {_answered_by(ans)} · {ans.kind}", rule]
    return "\n".join(out)


def show_sources(a: ContractAssistant) -> str:
    if not a.last:
        return "No question asked yet."
    rows = [f"Retrieval for: {a.last.retrieval_query}", "",
            f"  {'Clause':13s} {'Score':>6s}  {'How':24s} {'Title':40s} Matched words",
            f"  {'-' * 13} {'-' * 6}  {'-' * 24} {'-' * 40} {'-' * 13}"]
    for h in a.last.hits:
        score = "exact" if h.score == float("inf") else f"{h.score:.2f}"
        rows.append(f"  {h.clause.id:13s} {score:>6s}  {h.via:24s} {_clause_title(a, h.clause.id)[:40]:40s} "
                    f"{', '.join(h.matched)}")
    return "\n".join(rows)


def repl(a: ContractAssistant) -> None:
    print(banner(a))
    while True:
        try:
            q = input("\nyou> ").strip()
        except (EOFError, KeyboardInterrupt):
            print("\nbye")
            return
        if not q:
            continue
        cmd, _, arg = q.partition(" ")
        if cmd in ("/exit", "/quit"):
            print("bye")
            return
        if cmd == "/help":
            print(HELP)
        elif cmd == "/sources":
            print(show_sources(a))
        elif cmd == "/history":
            print("\n".join(f"  {i}. {t.question}" for i, t in enumerate(a.memory, 1)) or "  (empty)")
        elif cmd == "/clear":
            a.clear()
            print("Memory cleared.")
        elif cmd == "/provider":
            if arg:
                try:
                    a.provider = llm.choose_provider(arg.strip())
                except (ValueError, RuntimeError) as e:
                    print(f"Can't switch: {e}")
                    continue
            print(f"Provider: {a.provider} (model: {llm.model_for(a.provider)})")
        elif cmd.startswith("/"):
            print(f"Unknown command {cmd}. Try /help.")
        else:
            try:
                print("\n" + render(a, a.ask(q)))
            except Exception as e:                     # provider/network errors shouldn't kill the session
                print(f"Error from {a.provider}: {type(e).__name__}: {e}\nTry /provider extractive.")


def batch(a: ContractAssistant, questions_path: Path, out_path: Path) -> None:
    questions = [l.strip() for l in questions_path.read_text(encoding="utf-8").splitlines() if l.strip()]
    md = [f"# Contract assistant answers\n\nProvider: {a.provider} (model: {llm.model_for(a.provider)})\n"]
    for i, q in enumerate(questions, 1):
        a.clear()                                       # each listed question stands alone
        ans = a.ask(q)
        print(f"\nQ{i}\n" + render(a, ans))
        refs = "\n".join(f"- **{c}** — {_clause_title(a, c)}" for c in ans.citations) or "- none"
        warn = "".join(f"\n> ⚠ {w}" for w in ans.warnings)
        md.append(f"## Q{i}. {ans.question}\n\n**Answer**\n\n{ans.text}\n\n**Clause references**\n\n{refs}\n{warn}\n"
                  f"\n*Answered by: {_answered_by(ans)} · {ans.kind}*\n")
    out_path.write_text("\n".join(md), encoding="utf-8")
    print(f"\nWrote {out_path}")


def main() -> None:
    p = argparse.ArgumentParser(description="Shadowfax contract assistant")
    p.add_argument("--provider", choices=llm.PROVIDERS, help="force a provider (default: first API key found)")
    p.add_argument("--questions", type=Path, help="file with one question per line (batch mode)")
    p.add_argument("--out", type=Path, default=ROOT / "outputs" / "contract_answers.md")
    args = p.parse_args()
    a = ContractAssistant(args.provider)
    if args.questions:
        batch(a, args.questions, args.out)
    else:
        repl(a)


if __name__ == "__main__":
    main()
