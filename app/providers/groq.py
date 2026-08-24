import time
import structlog
from typing import Optional, Dict, Any
from app.core.config import settings
from app.providers.base import BaseProvider, ProviderResponse

log = structlog.get_logger()


class GroqProvider(BaseProvider):
    """
    Adapter for Groq Cloud API.
    Provides sub-second inference using Llama 3.3 70B, Llama 3 8B, and Mixtral models.
    """

    _MODELS = {
        "default": "groq/compound",
        "fast": "groq/compound-mini",
        "reasoning": "openai/gpt-oss-120b",
        "code": "qwen/qwen3.6-27b",
    }

    @property
    def name(self) -> str:
        return "groq"

    @property
    def supported_models(self) -> Dict[str, str]:
        return self._MODELS

    def is_available(self) -> bool:
        """Returns True if the GROQ_API_KEY environment setting is configured."""
        return bool(settings.groq_api_key and settings.groq_api_key.strip())

    def resolve_model(self, model: Optional[str] = None, job_type: Optional[str] = None) -> str:
        """
        Resolves model string based on direct model request or job role optimization.
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
        Executes generation using Groq API.
        """
        selected_model = self.resolve_model(model=model, job_type=job_type)

        if not self.is_available():
            log.warning("groq provider unavailable: missing GROQ_API_KEY")
            return ProviderResponse(
                provider=self.name,
                model=selected_model,
                status="error",
                error_message="GROQ_API_KEY is not configured.",
            )

        try:
            from groq import Groq

            client = Groq(api_key=settings.groq_api_key)

            start_time = time.time()
            chat_completion = client.chat.completions.create(
                messages=[{"role": "user", "content": prompt}],
                model=selected_model,
            )
            latency_ms = (time.time() - start_time) * 1000.0

            response_text = chat_completion.choices[0].message.content
            usage = getattr(chat_completion, "usage", None)

            input_tokens = usage.prompt_tokens if usage else None
            output_tokens = usage.completion_tokens if usage else None

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
            log.error("groq generation error", model=selected_model, error=str(e))
            return ProviderResponse(
                provider=self.name,
                model=selected_model,
                status="error",
                error_message=f"Groq generation error: {str(e)}",
            )
