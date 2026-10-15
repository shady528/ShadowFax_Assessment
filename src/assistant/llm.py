"""Answer writers: Claude, OpenAI or Gemini -- whichever API key is set -- else extractive (no LLM).

Pick order: RAG_PROVIDER env var / --provider flag if given, otherwise the first
provider whose key is set: Claude (ANTHROPIC_API_KEY) -> OpenAI (OPENAI_API_KEY)
-> Gemini (GEMINI_API_KEY or GOOGLE_API_KEY) -> extractive.
Models can be overridden with RAG_CLAUDE_MODEL / RAG_OPENAI_MODEL / RAG_GEMINI_MODEL.
Keys can also live in a .env file at the repo root (git-ignored); see .env.example.
"""
import os
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def load_dotenv(path: Path = ROOT / ".env") -> None:
    """Read KEY=VALUE lines from .env into the environment (real env vars win)."""
    if not path.exists():
        return
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        value = value.strip().strip('"').strip("'")
        if value:
            os.environ.setdefault(key.strip(), value)


load_dotenv()

GEMINI_FALLBACK_MODELS = ["gemini-3.6-flash", "gemini-3-flash-preview", "gemini-3.5-flash-lite", "gemini-3.1-flash-lite"]   # free tier: each model has its own quota
GEMINI_OVERLOAD_RETRIES = 2     # 503 "high demand" is temporary: wait 10s, 20s, then next model
GEMINI_BACKOFF_S = 10
GEMINI_TIMEOUT_MS = 30_000      # one call can never hang longer than this

PROVIDERS = ["claude", "openai", "gemini", "extractive"]
DEFAULT_MODELS = {
    "claude": "claude-opus-5-5",
    "openai": "gpt-4.1",
    "gemini": "gemini-3.5-flash",
    "extractive": "none (quotes clauses)",
}
KEY_VARS = {
    "claude": ["ANTHROPIC_API_KEY", "ANTHROPIC_AUTH_TOKEN"],
    "openai": ["OPENAI_API_KEY"],
    "gemini": ["GEMINI_API_KEY", "GOOGLE_API_KEY"],
}

SYSTEM_PROMPT = """You answer questions about Shadowfax's merchant contracts using ONLY the clauses provided in the user message.

Rules:
- Every factual statement must cite its clause id in square brackets, e.g. [M01-§4.2], [A.4], [M09-Ann1.1], [M03 preamble].
- A merchant's own clause overrides Schedule A where it says so. An annexure (amendment) overrides the clause it amends for the dates it covers; mention the dates.
- If the provided clauses do not contain the answer, reply exactly: "NOT IN DOCUMENTS: " followed by one sentence on what is missing. Do not use outside knowledge, and do not guess.
- If the question is partly answered, answer that part with citations and say plainly which part the documents don't cover.
- Be concise: 1-4 sentences, plain text, amounts in INR exactly as written in the clauses."""


def available(provider: str) -> bool:
    if provider == "extractive":
        return True
    return any(os.environ.get(v) for v in KEY_VARS[provider])


def choose_provider(requested: str | None = None) -> str:
    requested = requested or os.environ.get("RAG_PROVIDER")
    if requested:
        if requested not in PROVIDERS:
            raise ValueError(f"unknown provider {requested!r}; choose from {PROVIDERS}")
        if not available(requested):
            raise RuntimeError(f"{requested} selected but none of {KEY_VARS[requested]} is set")
        return requested
    return next(p for p in PROVIDERS if available(p))


def model_for(provider: str) -> str:
    return os.environ.get(f"RAG_{provider.upper()}_MODEL", DEFAULT_MODELS[provider])


def generate(provider: str, history: list[dict], user_message: str) -> tuple[str, str]:
    """Returns (answer text, model that actually answered).

    history: [{"role": "user"|"assistant", "content": str}, ...] (last few turns)."""
    if provider == "claude":
        return _claude(history, user_message)
    if provider == "openai":
        return _openai(history, user_message)
    if provider == "gemini":
        return _gemini(history, user_message)
    raise ValueError(provider)


def _claude(history: list[dict], user_message: str) -> tuple[str, str]:
    import anthropic

    client = anthropic.Anthropic()
    response = client.beta.messages.create(
        model=model_for("claude"),
        max_tokens=16000,
        system=SYSTEM_PROMPT,
        messages=history + [{"role": "user", "content": user_message}],
        output_config={"effort": "medium"},
        betas=["server-side-fallback-2026-07-01"],
        fallbacks="default",                     # on a safety refusal, the API retries on a fallback model
    )
    if response.stop_reason == "refusal":
        return "NOT IN DOCUMENTS: the model declined to answer this question.", response.model
    return "".join(b.text for b in response.content if b.type == "text").strip(), response.model


def _openai(history: list[dict], user_message: str) -> tuple[str, str]:
    from openai import OpenAI

    client = OpenAI()
    response = client.responses.create(
        model=model_for("openai"),
        instructions=SYSTEM_PROMPT,
        input=history + [{"role": "user", "content": user_message}],
        max_output_tokens=2000,
    )
    return response.output_text.strip(), model_for("openai")


def _gemini(history: list[dict], user_message: str) -> tuple[str, str]:
    """Try the main model, then the fallbacks. 429 (quota used up) -> next model at once;
    503 (overloaded) -> short wait and retry, then next model."""
    from google import genai
    from google.genai import errors, types

    # SDK's own retries off, so failures surface immediately and are handled here
    client = genai.Client(http_options=types.HttpOptions(
        timeout=GEMINI_TIMEOUT_MS, retry_options=types.HttpRetryOptions(attempts=1)))
    contents = [{"role": "model" if t["role"] == "assistant" else "user", "parts": [{"text": t["content"]}]}
                for t in history]
    contents.append({"role": "user", "parts": [{"text": user_message}]})
    config = types.GenerateContentConfig(system_instruction=SYSTEM_PROMPT, temperature=0.0, max_output_tokens=2000)

    extra = os.environ.get("RAG_GEMINI_FALLBACK_MODELS")
    models = list(dict.fromkeys([model_for("gemini")] + (extra.split(",") if extra else GEMINI_FALLBACK_MODELS)))
    last_error = None
    for model in models:
        for attempt in range(GEMINI_OVERLOAD_RETRIES + 1):
            try:
                response = client.models.generate_content(model=model, contents=contents, config=config)
                return (response.text or "").strip(), model
            except errors.APIError as e:
                last_error = e
                if e.code == 503 and attempt < GEMINI_OVERLOAD_RETRIES:
                    wait = GEMINI_BACKOFF_S * (attempt + 1)
                    print(f"  ({model}: overloaded, retrying in {wait}s)")
                    time.sleep(wait)
                    continue
                if e.code not in (429, 503):
                    raise
                reason = "quota used up" if e.code == 429 else "still overloaded"
            except Exception as e:                       # timeout / network
                last_error, reason = e, type(e).__name__
            if model != models[-1]:
                print(f"  ({model}: {reason}, trying {models[models.index(model) + 1]})")
            break
    raise last_error
