# 📚 Day 2 Technical Explanation & Architecture Reference

This document provides a line-by-line breakdown and explanation of **every new file created** and **every existing file modified** during the Day 2 implementation of the **AI Agent Reliability & Evaluation Platform**.

---

## 1. ⚙️ `app/core/config.py` *(Modified)*

### Code Snippet:

```Python
    # LLM Providers
    gemini_api_key: str = ""      # empty default = provider is optional
    groq_api_key: str = ""
    ollama_base_url: str = "http://localhost:11434"

    # Default routing & fallbacks
    default_provider: str = "gemini"
    fallback_providers: list[str] = ["groq", "ollama"]
```

### Technical Explanation:

- **`ollama_base_url`**: Defines the base URL for the local Ollama server (`http://localhost:11434`). This allows connecting to Ollama locally without hardcoding endpoints inside provider logic.
- **`default_provider`**: Configures `"gemini"` as the primary default provider for gateway requests when no provider is explicitly specified by the caller.
- **`fallback_providers`**: Defines the failover sequence (`["groq", "ollama"]`). If Gemini encounters rate limits (HTTP 429), downtime, or missing API keys, the Model Gateway automatically routes requests to Groq, and subsequently to Ollama.

---

## 2. 🔌 `app/providers/base.py` *(New File)*

### Code Snippet:

```python
from abc import ABC, abstractmethod
from typing import Optional, Dict, Any
from pydantic import BaseModel, Field


class ProviderResponse(BaseModel):
    """Standardized response container returned by all provider adapters."""
    provider: str = Field(description="Name of the provider (e.g., gemini, groq, ollama)")
    model: str = Field(description="Exact model name used for generation")
    response_text: Optional[str] = Field(default=None, description="Generated response string")
    latency_ms: float = Field(default=0.0, description="Latency in milliseconds")
    input_tokens: Optional[int] = Field(default=None, description="Input / prompt token count")
    output_tokens: Optional[int] = Field(default=None, description="Output / candidate token count")
    status: str = Field(default="success", description="Status of call: 'success' or 'error'")
    error_message: Optional[str] = Field(default=None, description="Error detail if call failed")
    fallback_used: bool = Field(default=False, description="True if this response resulted from fallback logic")
    primary_provider: Optional[str] = Field(default=None, description="Primary provider originally requested")


class BaseProvider(ABC):
    """Abstract Base Class for all LLM Provider Adapters."""

    @property
    @abstractmethod
    def name(self) -> str:
        """Returns the identifier name of the provider."""
        pass

    @property
    @abstractmethod
    def supported_models(self) -> Dict[str, str]:
        """Returns a dictionary mapping job roles ('default', 'fast', 'reasoning', 'code')."""
        pass

    @abstractmethod
    def is_available(self) -> bool:
        """Verifies if API key is present and operational."""
        pass

    @abstractmethod
    def generate(
        self,
        prompt: str,
        model: Optional[str] = None,
        job_type: Optional[str] = None,
        **kwargs: Any
    ) -> ProviderResponse:
        """Executes generation request."""
        pass
```

### Technical Explanation:

- **`ProviderResponse` (Pydantic Model)**: Establishes a strict contract for all provider outputs. Regardless of whether Google Gemini, Groq, or Ollama is called, the output is normalized into this standard shape containing text, latency, tokens, error state, and fallback metadata.
- **`BaseProvider` (Abstract Base Class)**: Enforces Python's `ABC` design pattern. Any provider class MUST implement `.name`, `.supported_models`, `.is_available()`, and `.generate()`. If a developer creates a new provider adapter and forgets one of these methods, Python raises an explicit TypeError at initialization time.

---

## 3. ♊ `app/providers/gemini.py` *(New File)*

### Code Snippet:

```python
import time
import structlog
from typing import Optional, Dict, Any
from app.core.config import settings
from app.providers.base import BaseProvider, ProviderResponse

log = structlog.get_logger()


class GeminiProvider(BaseProvider):
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
        return bool(settings.gemini_api_key and settings.gemini_api_key.strip())

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
        selected_model = self.resolve_model(model=model, job_type=job_type)

        if not self.is_available():
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
            return ProviderResponse(
                provider=self.name,
                model=selected_model,
                status="error",
                error_message=f"Gemini generation error: {str(e)}",
            )
```

### Technical Explanation:

- **`_MODELS` Dictionary**: Maps job roles to active Google Gemini models:
  - `fast`: `gemini-3.5-flash-lite` (optimized for sub-second classification)
  - `reasoning`: `gemini-3.1-pro-preview` (high-capacity reasoning for LLM-as-a-Judge)
  - `code`: `gemini-3.6-flash` (for JSON output structure checking)
- **`resolve_model()`**: Priority model resolution logic: explicit `model` string > `job_type` lookup > default model.
- **`generate()`**: Configures `google.generativeai` SDK, measures request duration in ms, extracts `prompt_token_count` & `candidates_token_count` safely, and returns a structured `ProviderResponse`. Any exception is caught and returned with `status="error"`.

---

## 4. ⚡ `app/providers/groq.py` *(New File)*

### Code Snippet:

```python
class GroqProvider(BaseProvider):
    _MODELS = {
        "default": "groq/compound",
        "fast": "groq/compound-mini",
        "reasoning": "openai/gpt-oss-120b",
        "code": "qwen/qwen3.6-27b",
    }

    @property
    def name(self) -> str:
        return "groq"

    def is_available(self) -> bool:
        return bool(settings.groq_api_key and settings.groq_api_key.strip())

    def generate(self, prompt: str, model=None, job_type=None, **kwargs) -> ProviderResponse:
        selected_model = self.resolve_model(model=model, job_type=job_type)
        if not self.is_available():
            return ProviderResponse(provider=self.name, model=selected_model, status="error", error_message="GROQ_API_KEY is not configured.")

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

            return ProviderResponse(
                provider=self.name,
                model=selected_model,
                response_text=response_text,
                latency_ms=round(latency_ms, 2),
                input_tokens=usage.prompt_tokens if usage else None,
                output_tokens=usage.completion_tokens if usage else None,
                status="success",
            )
        except Exception as e:
            return ProviderResponse(
                provider=self.name,
                model=selected_model,
                status="error",
                error_message=f"Groq generation error: {str(e)}",
            )
```

### Technical Explanation:

- **`_MODELS` Dictionary**: Configures active Groq Cloud models:
  - `fast`: `groq/compound-mini` (ultra-low latency)
  - `reasoning`: `openai/gpt-oss-120b` (large-scale reasoning)
  - `code`: `qwen/qwen3.6-27b` (high precision structured code/JSON generation)
- Uses `Groq(api_key).chat.completions.create(...)` and maps Groq token fields (`prompt_tokens` & `completion_tokens`) into the unified `ProviderResponse`.

---

## 5. 🦙 `app/providers/ollama.py` *(New File)*

### Code Snippet:

```python
class OllamaProvider(BaseProvider):
    _MODELS = {
        "default": "llama3",
        "fast": "gemma",
        "reasoning": "llama3",
        "code": "mistral",
    }

    def is_available(self) -> bool:
        try:
            url = f"{settings.ollama_base_url.rstrip('/')}/api/tags"
            response = httpx.get(url, timeout=1.5)
            return response.status_code == 200
        except Exception:
            return False

    def generate(self, prompt: str, model=None, job_type=None, **kwargs) -> ProviderResponse:
        selected_model = self.resolve_model(model=model, job_type=job_type)
        if not self.is_available():
            return ProviderResponse(
                provider=self.name,
                model=selected_model,
                status="error",
                error_message=f"Ollama server not available at {settings.ollama_base_url}.",
            )

        try:
            url = f"{settings.ollama_base_url.rstrip('/')}/api/generate"
            payload = {"model": selected_model, "prompt": prompt, "stream": False}

            start_time = time.time()
            response = httpx.post(url, json=payload, timeout=60.0)
            latency_ms = (time.time() - start_time) * 1000.0

            data = response.json()
            return ProviderResponse(
                provider=self.name,
                model=selected_model,
                response_text=data.get("response", ""),
                latency_ms=round(latency_ms, 2),
                input_tokens=data.get("prompt_eval_count"),
                output_tokens=data.get("eval_count"),
                status="success",
            )
        except Exception as e:
            return ProviderResponse(
                provider=self.name,
                model=selected_model,
                status="error",
                error_message=f"Ollama generation error: {str(e)}",
            )
```

### Technical Explanation:

- **Zero API Key Requirement**: Connects directly to local Ollama daemon (`http://localhost:11434`).
- **`is_available()`**: Sends a lightweight HTTP GET request to `/api/tags` with a 1.5s timeout. If Ollama is running locally, it returns `True`. If unavailable, it flags `False` without causing latency bottlenecks for cloud requests.
- Maps local Ollama token count attributes (`prompt_eval_count` and `eval_count`) into our standardized schema.

---

## 6. 🌐 `app/providers/gateway.py` *(New File)*

### Code Snippet:

```python
class ProviderStats(BaseModel):
    provider: str
    is_available: bool
    status: str  # HEALTHY, WARNING, UNAVAILABLE
    request_count: int = 0
    success_count: int = 0
    error_count: int = 0
    input_tokens: int = 0
    output_tokens: int = 0
    avg_latency_ms: float = 0.0
    supported_models: Dict[str, str] = Field(default_factory=dict)


class ModelGateway:
    def __init__(self):
        self._providers: Dict[str, BaseProvider] = {}
        self._stats: Dict[str, Dict[str, Any]] = {}
        self._register_default_providers()

    def generate(
        self,
        prompt: str,
        provider: Optional[str] = None,
        model: Optional[str] = None,
        job_type: Optional[str] = None,
        enable_fallback: bool = True,
        fallback_order: Optional[List[str]] = None,
    ) -> ProviderResponse:
        primary_name = (provider or settings.default_provider).lower()
        order = [primary_name]

        if enable_fallback:
            fallbacks = fallback_order or settings.fallback_providers
            for fb in fallbacks:
                if fb.lower() not in order:
                    order.append(fb.lower())

        last_response = None
        for idx, current_provider_name in enumerate(order):
            provider_adapter = self.get_provider(current_provider_name)
            is_fallback_attempt = idx > 0

            if not provider_adapter or not provider_adapter.is_available():
                continue

            res = provider_adapter.generate(prompt=prompt, model=model, job_type=job_type)
            self.record_telemetry(res)

            if res.status == "success":
                if is_fallback_attempt:
                    res.fallback_used = True
                    res.primary_provider = primary_name
                return res
            last_response = res

        return last_response
```

### Technical Explanation:

- **`ModelGateway`**: Central orchestrator managing provider registry, failover routing, and usage telemetry.
- **Automatic Fallback Logic**:
  1. Receives prompt request. Determines primary provider (e.g. Gemini).
  2. Constructs sequence order: `[primary, fallback_1, fallback_2]`.
  3. Executes request on primary provider.
  4. If primary provider fails (e.g. Rate Limit 429 or missing API key), logs error, skips to next fallback provider (e.g. Groq), and executes.
  5. If fallback succeeds, marks `fallback_used = True` and sets `primary_provider` so metrics accurately reflect failover execution.
- **`record_telemetry()`**: Accumulates total requests, success/error counts, token counts, and calculates average latency.
- **`get_telemetry()`**: Computes provider health classification (`HEALTHY`, `WARNING` if error rate > 20%, `UNAVAILABLE` if connection down or key missing).

---

## 7. 📦 `app/providers/__init__.py` *(New File)*

### Code Snippet:

```python
from app.providers.base import BaseProvider, ProviderResponse
from app.providers.gemini import GeminiProvider
from app.providers.groq import GroqProvider
from app.providers.ollama import OllamaProvider
from app.providers.gateway import ModelGateway, ProviderStats

__all__ = [
    "BaseProvider",
    "ProviderResponse",
    "GeminiProvider",
    "GroqProvider",
    "OllamaProvider",
    "ModelGateway",
    "ProviderStats",
]
```

### Technical Explanation:

Cleanly exports provider interface, response models, adapters, and gateway class for imports throughout the application.

---

## 8. 🗄️ `app/db/database.py` *(Modified)*

### Code Snippet:

```python
db_url = settings.database_url
try:
    if db_url.startswith("sqlite"):
        engine = create_engine(db_url, connect_args={"check_same_thread": False})
    else:
        engine = create_engine(db_url, pool_pre_ping=True)
except Exception:
    engine = create_engine("sqlite:///./evalplatform.db", connect_args={"check_same_thread": False})

from sqlalchemy import text

def get_db():
    try:
        db = SessionLocal()
        db.execute(text("SELECT 1"))
        try:
            yield db
        finally:
            db.close()
    except Exception:
        sqlite_engine = create_engine("sqlite:///./evalplatform.db", connect_args={"check_same_thread": False})
        Base.metadata.create_all(bind=sqlite_engine)
        FallbackSession = sessionmaker(autocommit=False, autoflush=False, bind=sqlite_engine)
        db = FallbackSession()
        try:
            yield db
        finally:
            db.close()
```

### Technical Explanation:

- **Resilient Database Fallback**: Previously, if local PostgreSQL container was stopped, API calls failed.
- Now, `get_db()` tests the connection with `SELECT 1`. If PostgreSQL connection drops or container is offline, it automatically falls back to a local SQLite database (`evalplatform.db`), creates tables if missing, and ensures zero application downtime.

---

## 9. 🚀 `app/main.py` *(Modified)*

### Code Snippet:

```python
gateway = ModelGateway()

@app.get("/providers/status", response_model=Dict[str, ProviderStats], tags=["Providers"])
def providers_status():
    """Returns complete real-time telemetry for all LLM providers."""
    return gateway.get_telemetry()


@app.post("/gateway/generate", response_model=LLMResponse, tags=["Gateway"])
def gateway_generate(request: GatewayGenerateRequest, db: Session = Depends(get_db)):
    """Executes LLM generation with job routing, fallback, telemetry, and DB persistence."""
    res = gateway.generate(
        prompt=request.prompt,
        provider=request.provider,
        model=request.model,
        job_type=request.job_type,
        enable_fallback=request.enable_fallback,
        fallback_order=request.fallback_order,
    )

    run = Run(
        provider=res.provider,
        model=res.model,
        prompt=request.prompt,
        response=res.response_text,
        latency_ms=res.latency_ms,
        input_tokens=res.input_tokens,
        output_tokens=res.output_tokens,
        status=res.status,
        error_message=res.error_message,
    )
    db.add(run)
    db.commit()
    db.refresh(run)

    return LLMResponse(
        run_id=run.id,
        provider=res.provider,
        model=res.model,
        prompt=request.prompt,
        response=res.response_text,
        latency_ms=res.latency_ms,
        input_tokens=res.input_tokens,
        output_tokens=res.output_tokens,
        status=res.status,
        fallback_used=res.fallback_used,
        primary_provider=res.primary_provider,
        error_message=res.error_message,
    )
```

### Technical Explanation:

- **Global Gateway Initialization**: Instantiates `gateway = ModelGateway()` on startup.
- **`/providers/status` Endpoint**: Exposes live health and telemetry metrics (`HEALTHY`, `WARNING`, `UNAVAILABLE`, token usage, model maps) for dashboard visibility.
- **`/gateway/generate` Endpoint**: Main evaluation endpoint supporting `job_type` selection ('fast', 'reasoning', 'code', 'default'), automatic failover execution, and database persistence to PostgreSQL/SQLite.
