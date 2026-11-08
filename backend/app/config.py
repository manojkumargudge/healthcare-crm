from pydantic import Field, ValidationError
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    # Required, no defaults: credentials must come from the environment / .env.
    database_url: str = Field(min_length=1)
    groq_api_key: str = Field(min_length=1)

    # Groq models (env: GROQ_MODEL, GROQ_MODEL_FAST). The assignment brief's
    # gemma2-9b-it and llama-3.3-70b-versatile are decommissioned on Groq.
    groq_model: str = Field(default="openai/gpt-oss-120b", min_length=1)  # agent loop, recommendations
    groq_model_fast: str = Field(default="openai/gpt-oss-20b", min_length=1)  # extraction / summarization
    frontend_origin: str = "http://localhost:5173"

    # extra="ignore": older .env files may still carry retired keys such as
    # GROQ_FALLBACK_MODEL; they must not stop the app from starting.
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")


def load_settings() -> Settings:
    """Build Settings, failing fast with a readable message if required values are missing."""
    try:
        return Settings()
    except ValidationError as exc:
        fields = sorted({str(err["loc"][0]).upper() for err in exc.errors()})
        raise RuntimeError(
            f"Missing or invalid required setting(s): {', '.join(fields)}. "
            "Set them in the environment or in backend/.env (see backend/.env.example)."
        ) from exc


settings = load_settings()
