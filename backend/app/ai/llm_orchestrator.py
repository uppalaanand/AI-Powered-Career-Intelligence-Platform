"""Multi-model fallback orchestrator.

    Grok (xAI)  ->  Google Gemini  ->  Groq  ->  controlled error
      primary        fallback 1       fallback 2

The rules this file exists to guarantee
---------------------------------------
1. **Sequential, never parallel.** One provider is asked at a time. The
   transcript is never fanned out to all three.
2. **Success stops the chain.** The moment a provider returns output that
   validates against ``LLMMeetingIntelligence``, the remaining providers are
   not called at all.
3. **Unconfigured providers are skipped, not called.** Availability is a local
   check on the environment - no probe request, no wasted quota.
4. **Failures move on rather than retry.** Bad key, exhausted quota, missing
   model, timeout, unreachable host, unusable JSON - all of them advance to the
   next provider instead of spending more calls on the broken one.
5. **Configuration problems stay visible.** If nothing is configured the caller
   gets a configuration error naming the variables to set; if the providers
   that *are* configured all fail, the error says which ones were tried and
   which were skipped for lack of a key.

Typical cost of one analysis: **one** provider request. Worst case with all
three configured and failing: three attempts, one per provider (each attempt may
spend at most ``LLM_PROVIDER_MAX_ATTEMPTS`` transport retries on genuinely
transient faults, and ``LLM_SCHEMA_RETRY_ATTEMPTS`` corrective re-prompts on
malformed JSON - set both to 1 for the strict minimum).
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional, Sequence, Tuple

from app.ai.llm_service import LLMService
from app.ai.providers import (
    PROVIDER_REGISTRY,
    LLMProvider,
    ProviderCategory,
    category_of,
    describe,
)
from app.config import get_logger, get_settings
from app.schemas.intelligence import LLMMeetingIntelligence
from app.utils.errors import (
    AppError,
    LLMAllProvidersFailedError,
    LLMInvalidResponseError,
    LLMNotConfiguredError,
)

logger = get_logger(__name__)


def build_default_providers() -> List[LLMProvider]:
    """Instantiate every known provider in the configured fallback order.

    Instantiation is cheap and opens no connections, so unconfigured providers
    are built too - they simply report ``is_configured == False`` and are
    skipped when the chain runs.
    """
    settings = get_settings()
    return [PROVIDER_REGISTRY[name]() for name in settings.llm_provider_names]


class LLMOrchestrator:
    """Runs the provider chain and reports which one produced the result.

    Exposes the same ``analyze_transcript`` signature as :class:`LLMService`, so
    callers upstream did not have to change: they still get
    ``(intelligence, metadata)``, now with ``metadata["provider"]`` telling them
    who answered.
    """

    def __init__(
        self,
        providers: Optional[Sequence[LLMProvider]] = None,
        *,
        schema_retry_attempts: Optional[int] = None,
    ) -> None:
        self._providers: List[LLMProvider] = (
            list(providers) if providers is not None else build_default_providers()
        )
        self._schema_attempts = (
            schema_retry_attempts
            if schema_retry_attempts is not None
            else get_settings().llm_schema_retry_attempts
        )

    # ---------------------------------------------------------- properties
    @property
    def providers(self) -> List[LLMProvider]:
        return list(self._providers)

    @property
    def configured_providers(self) -> List[LLMProvider]:
        """Providers with a key present, in fallback order. No network calls."""
        return [provider for provider in self._providers if provider.is_configured]

    @property
    def is_configured(self) -> bool:
        return bool(self.configured_providers)

    @property
    def model(self) -> str:
        """Model that would be tried first. Used for display only."""
        candidates = self.configured_providers or self._providers
        return candidates[0].model if candidates else ""

    # ------------------------------------------------------------- analyse
    async def analyze_transcript(
        self, transcript: str, *, meeting_title: str = ""
    ) -> Tuple[LLMMeetingIntelligence, Dict[str, Any]]:
        """Try each configured provider in order; return the first valid result."""
        if not (transcript or "").strip():
            # A caller mistake, not a provider failure: fail once, here, instead
            # of walking the whole chain to reach the same conclusion.
            raise LLMInvalidResponseError(
                "There is no transcript text to analyse.",
                code="EMPTY_TRANSCRIPT",
                status_code=422,
            )

        available = self.configured_providers
        skipped = [p.name for p in self._providers if not p.is_configured]

        if not available:
            raise LLMNotConfiguredError(
                "No AI provider is configured. Set at least one of "
                + ", ".join(f"{p.api_key_env}" for p in self._providers)
                + " in backend/.env.",
                details={"skipped": skipped, "category": ProviderCategory.NOT_CONFIGURED},
            )

        for name in skipped:
            logger.info("Skipping provider %s: no API key configured.", name)

        logger.info(
            "AI processing started. Provider chain: %s",
            " -> ".join(p.name for p in available),
        )

        attempts: List[Dict[str, str]] = []

        for position, provider in enumerate(available):
            logger.info(
                "Trying provider: %s (model=%s, %s of %s)",
                provider.label, provider.model, position + 1, len(available),
            )
            engine = LLMService(provider, schema_retry_attempts=self._schema_attempts)

            try:
                result, metadata = await engine.analyze_transcript(
                    transcript, meeting_title=meeting_title
                )
            except AppError as exc:
                category = category_of(exc)
                attempts.append({"provider": provider.name, "category": category})
                logger.warning(
                    "%s failed: %s. %s",
                    provider.label,
                    describe(category),
                    "Falling back to the next provider."
                    if position + 1 < len(available)
                    else "No providers left.",
                )
                continue
            except Exception as exc:  # noqa: BLE001 - a provider bug must not end the chain
                attempts.append({"provider": provider.name, "category": ProviderCategory.UNKNOWN})
                logger.exception("%s raised an unexpected error: %s", provider.label, exc)
                continue

            metadata["providers_attempted"] = [entry["provider"] for entry in attempts] + [
                provider.name
            ]
            metadata["providers_failed"] = list(attempts)
            metadata["providers_skipped"] = skipped
            metadata["fallback_used"] = bool(attempts)
            logger.info(
                "AI processing completed using %s (model=%s)%s",
                provider.label, provider.model,
                " after falling back" if attempts else "",
            )
            return result, metadata

        raise self._all_failed(attempts, skipped)

    # ------------------------------------------------------------- failure
    @staticmethod
    def _all_failed(
        attempts: List[Dict[str, str]], skipped: List[str]
    ) -> LLMAllProvidersFailedError:
        """One clear message naming what was tried and what was never set up."""
        tried = ", ".join(
            f"{entry['provider']} ({describe(entry['category'])})" for entry in attempts
        )
        message = (
            "AI analysis could not be completed at this time. We attempted the "
            f"configured AI providers ({tried}), but none returned a valid response."
        )
        if skipped:
            message += (
                " Not configured, so not attempted: " + ", ".join(skipped) + "."
                " Adding a key for one of those gives the analysis another chance."
            )
        else:
            message += " Please check your API configuration or try again later."

        logger.error("AI processing failed. Providers attempted: %s", tried or "none")
        return LLMAllProvidersFailedError(
            message, details={"attempted": attempts, "skipped": skipped}
        )

    # -------------------------------------------------------------- health
    async def check_connection(self, *, check_all: bool = False) -> Dict[str, Any]:
        """Operator-triggered connectivity report for ``/api/health/llm``.

        By default this makes exactly **one** live call - to the primary
        configured provider - and reports the others from local configuration
        only. ``check_all=True`` live-checks every configured provider, which
        costs one request each; nothing in the analysis path ever calls this.
        """
        available = self.configured_providers
        if not available:
            return {
                "configured": False,
                "reachable": False,
                "model": None,
                "message": (
                    "No AI provider is configured. Set XAI_API_KEY, GEMINI_API_KEY "
                    "or GROQ_API_KEY in backend/.env."
                ),
                "chain": [p.name for p in self._providers],
                "providers": [
                    {
                        "provider": p.name,
                        "label": p.label,
                        "configured": False,
                        "model": p.model,
                        "message": f"{p.api_key_env} is not set in backend/.env.",
                    }
                    for p in self._providers
                ],
            }

        reports: List[Dict[str, Any]] = []
        for provider in self._providers:
            if provider.is_configured and (check_all or provider is available[0]):
                reports.append(await provider.check_connection())
            else:
                reports.append(
                    {
                        "provider": provider.name,
                        "label": provider.label,
                        "configured": provider.is_configured,
                        "reachable": None,
                        "model": provider.model,
                        "message": (
                            "Configured. Not live-checked (pass ?all=true to test it)."
                            if provider.is_configured
                            else f"{provider.api_key_env} is not set in backend/.env."
                        ),
                    }
                )

        primary = next(report for report in reports if report["provider"] == available[0].name)
        return {
            "configured": True,
            "reachable": primary.get("reachable"),
            "model": primary.get("model"),
            "provider": primary.get("provider"),
            "message": primary.get("message"),
            "chain": [p.name for p in available],
            "providers": reports,
        }
