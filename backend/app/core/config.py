from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    supabase_url: str | None = None
    supabase_anon_key: str | None = None
    supabase_service_role_key: str | None = None
    azure_openai_api_key: str | None = None
    azure_openai_endpoint: str | None = None
    openai_api_version: str | None = None
    azure_openai_deployment: str | None = None
    llm_provider: str = "groq"
    llm_model: str = "llama-3.3-70b-versatile"
    groq_api_key: str | None = None
    query_timeout_seconds: float = 9.0
    token_encryption_key: str | None = None
    backend_base_url: str | None = None
    frontend_settings_url: str | None = None
    resend_api_key: str | None = None
    digest_from_email: str | None = None


@lru_cache
def get_settings() -> Settings:
    return Settings()
