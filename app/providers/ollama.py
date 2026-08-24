import time
import structlog
import httpx
from typing import Optional, Dict, Any
from app.core.config import settings
from app.providers.base import BaseProvider, ProviderResponse

log = structlog.get_logger()


class OllamaProvider(BaseProvider):
    """
    Adapter for local Ollama instance (http://localhost:11434).
    Zero API key required. Excellent local/offline evaluation fallback.
    """

    _MODELS = {
        "default": "llama3",
        "fast": "gemma",
        "reasoning": "llama3",
        "code": "mistral",
    }

    @property
    def name(self) -> str:
        return "ollama"

    @property
    def supported_models(self) -> Dict[str, str]:
        return self._MODELS

    def is_available(self) -> bool:
        """
        Checks if local Ollama daemon is active by pinging /api/tags endpoint.
        """
        try:
            url = f"{settings.ollama_base_url.rstrip('/')}/api/tags"
            response = httpx.get(url, timeout=1.5)
            return response.status_code == 200
        except Exception:
            return False

    def resolve_model(self, model: Optional[str] = None, job_type: Optional[str] = None) -> str:
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
        Executes text generation against local Ollama API.
        """
        selected_model = self.resolve_model(model=model, job_type=job_type)

        if not self.is_available():
            log.warning("ollama provider unavailable: local daemon not running", base_url=settings.ollama_base_url)
            return ProviderResponse(
                provider=self.name,
                model=selected_model,
                status="error",
                error_message=f"Ollama server not available at {settings.ollama_base_url}.",
            )

        try:
            url = f"{settings.ollama_base_url.rstrip('/')}/api/generate"
            payload = {
                "model": selected_model,
                "prompt": prompt,
                "stream": False
            }

            start_time = time.time()
            response = httpx.post(url, json=payload, timeout=60.0)
            latency_ms = (time.time() - start_time) * 1000.0

            if response.status_code != 200:
                raise RuntimeError(f"Ollama API returned HTTP status {response.status_code}: {response.text}")

            data = response.json()
            response_text = data.get("response", "")

            input_tokens = data.get("prompt_eval_count")
            output_tokens = data.get("eval_count")

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
            log.error("ollama generation error", model=selected_model, error=str(e))
            return ProviderResponse(
                provider=self.name,
                model=selected_model,
                status="error",
                error_message=f"Ollama generation error: {str(e)}",
            )
