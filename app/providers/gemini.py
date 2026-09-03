"""
Gemini Provider — Google Gemini API adapter with Priority Cascade Rotation.

Quota Problem:
  Gemini free tier gives ~20 requests/day PER MODEL.
  With 1 key and 1 model, that's only 20 calls — burned in a single evaluation run.

Solution: Priority Cascade Strategy
  Instead of using one model until it dies, we use the BEST model first across
  ALL available API keys, then step down to the next model tier:

  Step 1:  Key1 + gemini-3.5-flash  →  Key2 + gemini-3.5-flash     (best model)
  Step 2:  Key1 + gemini-3.6-flash  →  Key2 + gemini-3.6-flash     (2nd best)
  Step 3:  Key1 + flash-lite        →  Key2 + flash-lite            (3rd)
  Step 4:  Key1 + 3.1-pro-preview   →  Key2 + 3.1-pro-preview      (4th)
  Step 5:  → Groq fallback  (handled by ModelGateway, not here)
  Step 6:  → Ollama fallback (handled by ModelGateway, not here)

  With 2 keys × 4 models = ~160 requests/day before needing Groq/Ollama.

Key Pool:
  Supports multiple API keys via:
    - GEMINI_API_KEY (single, backwards compatible)
    - GEMINI_API_KEYS (comma-separated: "key1,key2,key3")
  Both are merged into a deduplicated pool.
"""

import time
import structlog
from typing import Optional, Dict, List, Any
from app.core.config import settings
from app.providers.base import BaseProvider, ProviderResponse

log = structlog.get_logger()


class GeminiProvider(BaseProvider):
    """
    Adapter for Google Gemini API with priority cascade rotation.

    Manages multiple API keys and models to maximize free-tier quota.
    When a key+model combination hits rate limit (HTTP 429), automatically
    tries the next key for the same model before stepping down to a lesser model.
    """

    # Job-type → model mapping (used when user specifies a job_type)
    _MODELS = {
        "default": "gemini-3.5-flash",
        "fast": "gemini-3.5-flash-lite",
        "reasoning": "gemini-3.1-pro-preview",
        "code": "gemini-3.6-flash",
    }

    # Priority-ordered model list: best model first, fallback models after.
    # Each model gets ~20 req/day on the free tier, so 4 models = ~80 req/key.
    _MODEL_PRIORITY = [
        "gemini-3.5-flash",        # Tier 1: Best overall quality
        "gemini-3.6-flash",        # Tier 2: Good for code + general
        "gemini-3.5-flash-lite",   # Tier 3: Fast, lightweight
        "gemini-3.1-pro-preview",  # Tier 4: Reasoning-heavy (slower)
    ]

    def __init__(self):
        """Initialize the key pool and rotation state."""
        self._key_pool: List[str] = []
        self._exhausted_combos: set = set()  # tracks "key:model" combos that got 429'd
        self._cascade_position: int = 0       # current position in the cascade
        self._load_key_pool()

        log.info(
            "gemini provider initialized",
            key_count=len(self._key_pool),
            models=self._MODEL_PRIORITY,
            total_quota_estimate=len(self._key_pool) * len(self._MODEL_PRIORITY) * 20,
        )

    def _load_key_pool(self):
        """
        Load all available API keys from config into a deduplicated pool.

        Sources:
          - GEMINI_API_KEY (single key, backwards compatible)
          - GEMINI_API_KEYS (comma-separated for multiple accounts)
        """
        keys = []

        # Primary key (backwards compatible with Days 1-5)
        if settings.gemini_api_key and settings.gemini_api_key.strip():
            keys.append(settings.gemini_api_key.strip())

        # Additional keys from comma-separated config
        if settings.gemini_api_keys and settings.gemini_api_keys.strip():
            for k in settings.gemini_api_keys.split(","):
                k = k.strip()
                if k and k not in keys:
                    keys.append(k)

        self._key_pool = keys

    @property
    def name(self) -> str:
        return "gemini"

    @property
    def supported_models(self) -> Dict[str, str]:
        return self._MODELS

    def is_available(self) -> bool:
        """Returns True if at least one API key is configured."""
        return len(self._key_pool) > 0

    def resolve_model(self, model: Optional[str] = None, job_type: Optional[str] = None) -> str:
        """
        Determines the exact model string to use.

        Priority:
          1. Explicit model string from the caller
          2. Job-type mapping (fast → flash-lite, reasoning → pro-preview, etc.)
          3. Best available model from the priority cascade
        """
        if model:
            return model
        if job_type and job_type in self._MODELS:
            return self._MODELS[job_type]
        return self._MODELS["default"]

    def _build_cascade(self, preferred_model: Optional[str] = None) -> List[tuple]:
        """
        Build the full priority cascade: list of (api_key, model) pairs to try.

        Strategy: For each model tier (best → worst), try ALL keys before moving
        to the next model. This maximizes usage of the best model.

        If a preferred_model is given (user specified a model or job_type),
        that model is tried first across all keys, THEN the cascade continues
        with remaining models.

        Returns:
            List of (api_key, model_name) tuples in try-order.
        """
        cascade = []

        # Determine model order
        if preferred_model and preferred_model in self._MODEL_PRIORITY:
            # Put the preferred model first, then the rest in priority order
            model_order = [preferred_model] + [
                m for m in self._MODEL_PRIORITY if m != preferred_model
            ]
        elif preferred_model:
            # User requested a model not in our priority list — try it first with all keys,
            # then fall back to the standard cascade
            model_order = [preferred_model] + self._MODEL_PRIORITY
        else:
            model_order = self._MODEL_PRIORITY

        # For each model tier, try every key
        for model_name in model_order:
            for api_key in self._key_pool:
                combo_id = f"{api_key[-6:]}:{model_name}"  # short key suffix for logging
                if combo_id not in self._exhausted_combos:
                    cascade.append((api_key, model_name))

        # If rotation is disabled, just use the first key + resolved model
        if not settings.gemini_rotate_models and preferred_model:
            first_key = self._key_pool[0] if self._key_pool else ""
            return [(first_key, preferred_model)]

        return cascade

    def generate(
        self,
        prompt: str,
        model: Optional[str] = None,
        job_type: Optional[str] = None,
        **kwargs: Any
    ) -> ProviderResponse:
        """
        Execute generation using Google Gemini with priority cascade rotation.

        Flow:
          1. Resolve preferred model from model/job_type/default
          2. Build cascade: (key1,best) → (key2,best) → (key1,2nd) → (key2,2nd) → ...
          3. Try each combo; on 429/quota error → mark combo as exhausted, try next
          4. On success → return response
          5. If all combos exhausted → return error (gateway will fallback to Groq/Ollama)
        """
        preferred_model = self.resolve_model(model=model, job_type=job_type)

        if not self.is_available():
            log.warning("gemini provider unavailable: no API keys configured")
            return ProviderResponse(
                provider=self.name,
                model=preferred_model,
                status="error",
                error_message="No Gemini API keys configured. Set GEMINI_API_KEY or GEMINI_API_KEYS in .env",
            )

        cascade = self._build_cascade(preferred_model)

        if not cascade:
            log.warning("all gemini key+model combinations exhausted for this session")
            return ProviderResponse(
                provider=self.name,
                model=preferred_model,
                status="error",
                error_message=(
                    f"All Gemini API key+model combinations are rate-limited. "
                    f"Keys: {len(self._key_pool)}, Models: {len(self._MODEL_PRIORITY)}, "
                    f"Exhausted combos: {len(self._exhausted_combos)}. "
                    f"Gateway will fallback to Groq/Ollama."
                ),
            )

        last_error = None

        for attempt_idx, (api_key, selected_model) in enumerate(cascade):
            combo_id = f"{api_key[-6:]}:{selected_model}"

            try:
                import google.generativeai as genai

                genai.configure(api_key=api_key)
                gemini_instance = genai.GenerativeModel(selected_model)

                start_time = time.time()
                result = gemini_instance.generate_content(prompt)
                latency_ms = (time.time() - start_time) * 1000.0

                response_text = result.text if hasattr(result, "text") else str(result)

                # Safely extract token usage metadata
                usage = getattr(result, "usage_metadata", None)
                input_tokens = getattr(usage, "prompt_token_count", None)
                output_tokens = getattr(usage, "candidates_token_count", None)

                log.info(
                    "gemini generation succeeded",
                    model=selected_model,
                    key_suffix=api_key[-6:],
                    attempt=attempt_idx + 1,
                    latency_ms=round(latency_ms, 2),
                )

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
                error_str = str(e).lower()
                is_rate_limit = (
                    "429" in str(e)
                    or "quota" in error_str
                    or "resource has been exhausted" in error_str
                    or "rate limit" in error_str
                    or "too many requests" in error_str
                )

                if is_rate_limit:
                    # Mark this key+model combo as exhausted and try the next one
                    self._exhausted_combos.add(combo_id)
                    log.warning(
                        "gemini rate-limited, trying next cascade step",
                        model=selected_model,
                        key_suffix=api_key[-6:],
                        attempt=attempt_idx + 1,
                        remaining_combos=len(cascade) - attempt_idx - 1,
                        error=str(e)[:100],
                    )
                    last_error = e
                    continue
                else:
                    # Non-rate-limit error (bad prompt, safety block, etc.)
                    log.error(
                        "gemini generation error (non-quota)",
                        model=selected_model,
                        error=str(e),
                    )
                    return ProviderResponse(
                        provider=self.name,
                        model=selected_model,
                        status="error",
                        error_message=f"Gemini error: {str(e)}",
                    )

        # All cascade steps exhausted — every key+model got rate-limited
        log.error(
            "all gemini cascade steps exhausted",
            total_keys=len(self._key_pool),
            total_models=len(self._MODEL_PRIORITY),
            exhausted=len(self._exhausted_combos),
        )
        return ProviderResponse(
            provider=self.name,
            model=preferred_model,
            status="error",
            error_message=(
                f"All Gemini combinations rate-limited ({len(self._exhausted_combos)} combos exhausted). "
                f"Last error: {str(last_error)[:200] if last_error else 'unknown'}"
            ),
        )

    def get_quota_status(self) -> Dict[str, Any]:
        """
        Returns current quota usage status for monitoring/debugging.

        Useful for the /providers/status endpoint to show how many
        key+model combinations are still available.
        """
        total_combos = len(self._key_pool) * len(self._MODEL_PRIORITY)
        exhausted = len(self._exhausted_combos)
        return {
            "total_keys": len(self._key_pool),
            "total_models": len(self._MODEL_PRIORITY),
            "total_combinations": total_combos,
            "exhausted_combinations": exhausted,
            "remaining_combinations": total_combos - exhausted,
            "exhausted_details": list(self._exhausted_combos),
            "model_priority": self._MODEL_PRIORITY,
        }

    def reset_exhausted(self):
        """
        Reset all exhausted combos — useful at the start of a new day
        when Gemini quotas reset, or called manually via API.
        """
        count = len(self._exhausted_combos)
        self._exhausted_combos.clear()
        log.info("gemini exhausted combos reset", cleared=count)
