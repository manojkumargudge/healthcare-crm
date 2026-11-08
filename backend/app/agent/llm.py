"""
Groq LLM clients for the agent.

Models are configured only through settings (env vars), never hardcoded here:

  GROQ_MODEL       main model: the tool-calling agent loop and
                   next-best-action recommendations.
  GROQ_MODEL_FAST  fast model: structured extraction / summarization
                   (e.g. turning a rep's notes into CRM fields).

Groq retires models over time (console.groq.com/docs/deprecations). Every call
goes through `ainvoke_resilient`: if Groq says the requested model is
decommissioned or unknown, the call is retried once with the *other*
configured model. Any other error (auth, rate limit, network) is raised
immediately rather than silently retried.
"""
import logging

from langchain_groq import ChatGroq

from app.config import settings

logger = logging.getLogger("hcp_crm.agent.llm")

_DECOMMISSION_HINTS = (
    "decommission",
    "does not exist",
    "model_not_found",
    "invalid model",
    "has been deprecated",
    "not found",
)


def _dedupe(seq):
    seen, out = set(), []
    for x in seq:
        if x and x not in seen:
            seen.add(x)
            out.append(x)
    return out


def model_chain(fast: bool = False) -> list[str]:
    """Models to try, in order. Read at call time so settings changes apply."""
    main, quick = settings.groq_model, settings.groq_model_fast
    return _dedupe([quick, main] if fast else [main, quick])


def get_llm(temperature: float = 0.2) -> ChatGroq:
    """Main model (GROQ_MODEL). Prefer `ainvoke_resilient`, which survives a
    Groq-side model retirement."""
    return ChatGroq(api_key=settings.groq_api_key, model=settings.groq_model, temperature=temperature)


def get_fast_llm(temperature: float = 0.2) -> ChatGroq:
    """Fast model (GROQ_MODEL_FAST) for extraction/summarization."""
    return ChatGroq(api_key=settings.groq_api_key, model=settings.groq_model_fast, temperature=temperature)


async def ainvoke_resilient(messages, temperature: float = 0.2, tools=None, fast: bool = False):
    """Invoke Groq with the main model (or the fast one if `fast=True`).

    Falls back to the other configured model only when Groq reports the
    requested one as decommissioned/unknown; every other error is raised.

    Returns (response, model_name_actually_used).
    """
    last_err = None
    for model_name in model_chain(fast):
        llm = ChatGroq(api_key=settings.groq_api_key, model=model_name, temperature=temperature)
        if tools:
            llm = llm.bind_tools(tools)  # type: ignore[assignment]  # bind_tools returns a Runnable, not ChatGroq; only .ainvoke is used after
        try:
            response = await llm.ainvoke(messages)
            return response, model_name
        except Exception as e:
            msg = str(e).lower()
            if any(hint in msg for hint in _DECOMMISSION_HINTS):
                logger.warning("Groq model '%s' unavailable (%s) — trying the other configured model.", model_name, e)
                last_err = e
                continue
            raise
    raise last_err or RuntimeError("No Groq model configured.")
