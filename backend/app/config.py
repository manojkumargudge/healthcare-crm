from pydantic import Field, ValidationError
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    # Required, no defaults: credentials must come from the environment / .env.
    database_url: str = Field(min_length=1)
    groq_api_key: str = Field(min_length=1)

    groq_model: str = "gemma2-9b-it"
    groq_fallback_model: str = "llama-3.3-70b-versatile"
    frontend_origin: str = "http://localhost:5173"

    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8")


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
