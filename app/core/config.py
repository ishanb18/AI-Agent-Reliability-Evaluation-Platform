from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """
    All configuration is loaded from environment variables (or .env file).
    Pydantic-settings automatically reads the .env file and maps each
    variable to the matching field below. If a required variable is missing,
    the app crashes at startup with a clear error — not silently at runtime.
    """

    # Database
    database_url: str

    # LLM Providers
    gemini_api_key: str = ""      # empty default = provider is optional
    gemini_api_keys: str = ""     # comma-separated extra keys for quota pooling: "key1,key2"
    gemini_rotate_models: bool = True  # rotate across models to spread quota (20 req/model/day)
    groq_api_key: str = ""
    ollama_base_url: str = "http://localhost:11434"

    # Default routing & fallbacks
    default_provider: str = "gemini"
    fallback_providers: list[str] = ["groq", "ollama"]

    # App
    app_env: str = "development"
    app_debug: bool = True

    # Tell pydantic-settings to read from a .env file in the project root
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8")


# Create a single shared instance — imported everywhere else as:
#   from app.core.config import settings
settings = Settings()
