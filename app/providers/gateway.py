import structlog
from typing import Dict, List, Optional, Any
from pydantic import BaseModel, Field

from app.core.config import settings
from app.providers.base import BaseProvider, ProviderResponse
from app.providers.gemini import GeminiProvider
from app.providers.groq import GroqProvider
from app.providers.ollama import OllamaProvider

log = structlog.get_logger()


class ProviderStats(BaseModel):
    """Telemetry data tracking usage, performance, and health per provider."""
    provider: str
    is_available: bool
    status: str = Field(description="Health status: HEALTHY, WARNING, UNAVAILABLE")
    request_count: int = 0
    success_count: int = 0
    error_count: int = 0
    input_tokens: int = 0
    output_tokens: int = 0
    avg_latency_ms: float = 0.0
    supported_models: Dict[str, str] = Field(default_factory=dict)


class ModelGateway:
    """
    Model Gateway Service.
    Coordinates requests across provider adapters, manages job-based model routing,
    executes automatic fallback sequences upon failure, and tracks quota telemetry.
    """

    def __init__(self):
        self._providers: Dict[str, BaseProvider] = {}
        self._stats: Dict[str, Dict[str, Any]] = {}
        self._register_default_providers()

    def _register_default_providers(self):
        """Initializes and registers standard providers (Gemini, Groq, Ollama)."""
        defaults = [GeminiProvider(), GroqProvider(), OllamaProvider()]
        for provider in defaults:
            self.register_provider(provider)

    def register_provider(self, provider: BaseProvider):
        """Registers a provider adapter into the gateway registry."""
        self._providers[provider.name] = provider
        if provider.name not in self._stats:
            self._stats[provider.name] = {
                "request_count": 0,
                "success_count": 0,
                "error_count": 0,
                "input_tokens": 0,
                "output_tokens": 0,
                "total_latency_ms": 0.0,
            }
        log.info("registered provider", provider=provider.name, available=provider.is_available())

    def get_provider(self, name: str) -> Optional[BaseProvider]:
        """Retrieves provider adapter by name."""
        return self._providers.get(name.lower())

    def record_telemetry(self, response: ProviderResponse):
        """Updates in-memory telemetry stats from a ProviderResponse."""
        p_name = response.provider.lower()
        if p_name not in self._stats:
            self._stats[p_name] = {
                "request_count": 0,
                "success_count": 0,
                "error_count": 0,
                "input_tokens": 0,
                "output_tokens": 0,
                "total_latency_ms": 0.0,
            }

        stats = self._stats[p_name]
        stats["request_count"] += 1

        if response.status == "success":
            stats["success_count"] += 1
            stats["total_latency_ms"] += response.latency_ms
            if response.input_tokens:
                stats["input_tokens"] += response.input_tokens
            if response.output_tokens:
                stats["output_tokens"] += response.output_tokens
        else:
            stats["error_count"] += 1

    def generate(
        self,
        prompt: str,
        provider: Optional[str] = None,
        model: Optional[str] = None,
        job_type: Optional[str] = None,
        enable_fallback: bool = True,
        fallback_order: Optional[List[str]] = None,
    ) -> ProviderResponse:
        """
        Main entry point for generating text.
        Attempts request on primary provider; if failure occurs and enable_fallback is True,
        sequentially tries fallback providers until one succeeds.
        """
        primary_name = (provider or settings.default_provider).lower()
        order = [primary_name]

        if enable_fallback:
            fallbacks = fallback_order or settings.fallback_providers
            for fb in fallbacks:
                fb_clean = fb.lower()
                if fb_clean not in order:
                    order.append(fb_clean)

        last_response: Optional[ProviderResponse] = None

        for idx, current_provider_name in enumerate(order):
            provider_adapter = self.get_provider(current_provider_name)
            is_fallback_attempt = idx > 0

            if not provider_adapter or not provider_adapter.is_available():
                log.warning(
                    "provider bypassed (unavailable or unregistered)",
                    provider=current_provider_name,
                    is_fallback=is_fallback_attempt,
                )
                continue

            log.info(
                "attempting provider generation",
                provider=current_provider_name,
                job_type=job_type,
                is_fallback=is_fallback_attempt,
            )

            res = provider_adapter.generate(prompt=prompt, model=model, job_type=job_type)
            self.record_telemetry(res)

            if res.status == "success":
                if is_fallback_attempt:
                    res.fallback_used = True
                    res.primary_provider = primary_name
                    log.info("fallback generation succeeded", used_provider=current_provider_name, primary=primary_name)
                return res

            last_response = res
            log.warn("provider execution failed, trying next fallback if available", provider=current_provider_name, error=res.error_message)

        # If all providers failed or were unavailable
        if last_response:
            last_response.primary_provider = primary_name
            return last_response

        return ProviderResponse(
            provider=primary_name,
            model=model or "unknown",
            status="error",
            error_message=f"All specified providers ({', '.join(order)}) failed or are unavailable.",
            primary_provider=primary_name,
        )

    def get_telemetry(self) -> Dict[str, ProviderStats]:
        """
        Returns snapshot of all registered providers, availability status,
        model maps, latency averages, and health classification.
        """
        result = {}
        for name, adapter in self._providers.items():
            is_avail = adapter.is_available()
            raw_stats = self._stats.get(name, {})

            reqs = raw_stats.get("request_count", 0)
            succs = raw_stats.get("success_count", 0)
            errs = raw_stats.get("error_count", 0)
            tot_lat = raw_stats.get("total_latency_ms", 0.0)

            avg_lat = round(tot_lat / succs, 2) if succs > 0 else 0.0

            # Health classification logic
            if not is_avail:
                health = "UNAVAILABLE"
            elif reqs > 0 and (errs / reqs) > 0.2:
                health = "WARNING"
            else:
                health = "HEALTHY"

            result[name] = ProviderStats(
                provider=name,
                is_available=is_avail,
                status=health,
                request_count=reqs,
                success_count=succs,
                error_count=errs,
                input_tokens=raw_stats.get("input_tokens", 0),
                output_tokens=raw_stats.get("output_tokens", 0),
                avg_latency_ms=avg_lat,
                supported_models=adapter.supported_models,
            )
        return result
