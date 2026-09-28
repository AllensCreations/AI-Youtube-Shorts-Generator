"""Local LLM backend — OpenAI or Gemini, selected by LLM_PROVIDER."""
from ..config import (
    GEMINI_MODEL,
    LLM_PROVIDER,
    OPENAI_MODEL,
    require_gemini_key,
    require_openai_key,
)


def call_openai_llm(prompt: str) -> str:
    """OpenAI Chat Completions backend used by --mode local."""
    try:
        from openai import OpenAI  # type: ignore
    except ImportError as e:
        raise RuntimeError(
            "openai is required for --mode local. Install it with:\n"
            "    pip install -r requirements-local.txt"
        ) from e

    client = OpenAI(api_key=require_openai_key())
    response = client.chat.completions.create(
        model=OPENAI_MODEL,
        temperature=0.7,
        messages=[{"role": "user", "content": prompt}],
    )
    return response.choices[0].message.content or ""


def call_gemini_llm(prompt: str) -> str:
    """Gemini backend used by --mode local when LLM_PROVIDER=gemini."""
    import time

    try:
        from google import genai  # type: ignore
    except ImportError as e:
        raise RuntimeError(
            "google-genai is required for LLM_PROVIDER=gemini. Install it with:\n"
            "    pip install -r requirements-local.txt"
        ) from e

    client = genai.Client(api_key=require_gemini_key())
    for attempt in range(5):
        try:
            response = client.models.generate_content(
                model=GEMINI_MODEL,
                contents=prompt,
                config={
                    "temperature": 0.2,
                    "response_mime_type": "application/json",
                    "max_output_tokens": 8192,
                },
            )
            return response.text or ""
        except Exception as e:
            err_str = str(e)
            if attempt < 4 and any(code in err_str for code in ("503", "UNAVAILABLE", "429", "RESOURCE_EXHAUSTED")):
                delay = 3 * (attempt + 1)
                print(f"[llm/gemini] API busy or high demand ({err_str[:40]}...), retrying in {delay}s...", flush=True)
                time.sleep(delay)
                continue
            raise
    return ""


def call_local_llm(prompt: str) -> str:
    """Dispatch to the configured local LLM provider with intelligent fallback."""
    provider = (LLM_PROVIDER or "gemini").strip().lower()
    if provider == "gemini":
        try:
            return call_gemini_llm(prompt)
        except Exception as e:
            from ..config import OPENAI_API_KEY
            if OPENAI_API_KEY and OPENAI_API_KEY != "your_openai_key_here":
                print(f"[llm/fallback] Gemini unavailable ({e}); switching to OpenAI...", flush=True)
                return call_openai_llm(prompt)
            raise
    if provider == "openai":
        try:
            return call_openai_llm(prompt)
        except Exception as e:
            from ..config import GEMINI_API_KEY
            if GEMINI_API_KEY and GEMINI_API_KEY != "your_gemini_key_here":
                print(f"[llm/fallback] OpenAI unavailable ({e}); switching to Gemini...", flush=True)
                return call_gemini_llm(prompt)
            raise
    raise RuntimeError(
        f"Unknown LLM_PROVIDER={provider!r}. Use 'openai' or 'gemini'."
    )
