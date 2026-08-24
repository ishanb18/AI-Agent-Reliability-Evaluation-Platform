import time
import structlog
from typing import Optional, Dict, Any
from app.core.config import settings
from app.providers.base import BaseProvider, ProviderResponse

log = structlog.get_logger()


class GeminiProvider(BaseProvider):
    """
    Adapter for Google Gemini API.
    Provides multi-model support tuned for specific workloads (fast, reasoning, code).
    """

    _MODELS = {
        "default": "gemini-3.5-flash",
        "fast": "gemini-3.5-flash-lite",
        "reasoning": "gemini-3.1-pro-preview",
        "code": "gemini-3.6-flash",
    }

    @property
    def name(self) -> str:
        return "gemini"

    @property
    def supported_models(self) -> Dict[str, str]:
        return self._MODELS

    def is_available(self) -> bool:
        """Returns True if the GEMINI_API_KEY environment setting is configured."""
        return bool(settings.gemini_api_key and settings.gemini_api_key.strip())

    def resolve_model(self, model: Optional[str] = None, job_type: Optional[str] = None) -> str:
        """
        Determines the exact model string to use based on requested model or job_type.
        """
        if model:
            return model
        if job_type and job_type in self._MODELS:
            return self._MODELS[job_type]
        return self._MODELS["default"]

    def generate(
        self,
        prompt: str,
        model: Optional[str] = None,
        job_type: Optional[str] = None,
        **kwargs: Any
    ) -> ProviderResponse:
        """
        Executes generation using Google Gemini.
        """
        selected_model = self.resolve_model(model=model, job_type=job_type)

        if not self.is_available():
            log.warning("gemini provider unavailable: missing GEMINI_API_KEY")
            return ProviderResponse(
                provider=self.name,
                model=selected_model,
                status="error",
                error_message="GEMINI_API_KEY is not configured.",
            )

        try:
            import google.generativeai as genai

            genai.configure(api_key=settings.gemini_api_key)
            gemini_instance = genai.GenerativeModel(selected_model)

            start_time = time.time()
            result = gemini_instance.generate_content(prompt)
            latency_ms = (time.time() - start_time) * 1000.0

            response_text = result.text if hasattr(result, "text") else str(result)

            # Safely extract token usage metadata if available
            usage = getattr(result, "usage_metadata", None)
            input_tokens = getattr(usage, "prompt_token_count", None)
            output_tokens = getattr(usage, "candidates_token_count", None)

            return ProviderResponse(
                provider=self.name,
                model=selected_model,
                response_text=response_text,
                latency_ms=round(latency_ms, 2),
                input_tokens=input_tokens,
                output_tokens=output_tokens,
                status="success",
            )

        except Exception as e:
            log.error("gemini generation error", model=selected_model, error=str(e))
            return ProviderResponse(
                provider=self.name,
                model=selected_model,
                status="error",
                error_message=f"Gemini generation error: {str(e)}",
            )
