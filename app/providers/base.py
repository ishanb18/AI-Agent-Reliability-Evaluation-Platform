from abc import ABC, abstractmethod
from typing import Optional, Dict, Any
from pydantic import BaseModel, Field


class ProviderResponse(BaseModel):
    """
    Standardized response container returned by all provider adapters.
    Ensures a consistent interface regardless of underlying SDK differences.
    """
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
    """
    Abstract Base Class for all LLM Provider Adapters.
    Enforces a common interface for availability checks and text generation.
    """

    @property
    @abstractmethod
    def name(self) -> str:
        """Returns the identifier name of the provider."""
        pass

    @property
    @abstractmethod
    def supported_models(self) -> Dict[str, str]:
        """
        Returns a dictionary mapping job roles ('default', 'fast', 'reasoning', 'code')
        to exact provider model identifiers.
        """
        pass

    @abstractmethod
    def is_available(self) -> bool:
        """
        Verifies if the provider is properly configured (e.g., API key present)
        and operational.
        """
        pass

    @abstractmethod
    def generate(
        self,
        prompt: str,
        model: Optional[str] = None,
        job_type: Optional[str] = None,
        **kwargs: Any
    ) -> ProviderResponse:
        """
        Executes a text generation request using the specified model or job_type.

        :param prompt: The input text prompt to process.
        :param model: Specific model identifier. If None, job_type or default model is used.
        :param job_type: Job role ('default', 'fast', 'reasoning', 'code') to auto-select best model.
        :return: Standardized ProviderResponse object.
        """
        pass
