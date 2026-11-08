from pathlib import Path
from typing import Optional

from pydantic import Field, ValidationError, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

from app.db_url import normalize_database_url, split_ssl

# The one .env file, at the repo root (shared with docker compose). Resolved
# from this file's location so it works regardless of the working directory.
# Inside the Docker image this path does not exist; compose passes env vars.
ENV_FILE = Path(__file__).resolve().parents[2] / ".env"


class Settings(BaseSettings):
    # Unknown keys in .env fail startup (pydantic-settings default extra="forbid"),
    # so a typo or a retired key is caught instead of silently ignored. Every key
    # in .env.example must therefore be declared here, including compose-only ones.

    groq_api_key: str = Field(min_length=1)

    # Either DATABASE_URL (compose, CI, tests) or the POSTGRES_* values below.
    database_url: str = ""  # empty -> built from POSTGRES_* by the validator below
    postgres_user: Optional[str] = None
    postgres_password: Optional[str] = None
    postgres_db: Optional[str] = None
    postgres_host: str = "127.0.0.1"
    postgres_port: int = 5432  # host port published by compose

    # Groq models (env: GROQ_MODEL, GROQ_MODEL_FAST). The assignment brief's
    # gemma2-9b-it and llama-3.3-70b-versatile are decommissioned on Groq.
    groq_model: str = Field(default="openai/gpt-oss-120b", min_length=1)  # agent loop, recommendations
    groq_model_fast: str = Field(default="openai/gpt-oss-20b", min_length=1)  # extraction / summarization
    # Speech-to-text for voice notes. Active Groq Whisper models (checked via the Groq
    # /models API): whisper-large-v3-turbo (faster) and whisper-large-v3 (more accurate).
    groq_transcribe_model: str = Field(default="whisper-large-v3-turbo", min_length=1)

    frontend_origin: str = "http://localhost:5173"
    # Extra allowed browser origins, comma-separated (e.g. the Render static site URL).
    cors_origins: str = ""

    # Apply backend/migrations/*.sql on startup (idempotent). Set false to migrate by hand.
    run_migrations_on_startup: bool = True

    # Auth. JWT_SECRET is required (no default); generate one with:
    #   python -c "import secrets; print(secrets.token_urlsafe(48))"
    jwt_secret: str = Field(min_length=32)
    access_token_minutes: int = Field(default=15, ge=1, le=240)  # short-lived access tokens
    # Seed admin: created at startup (and by migration 008) if both are set.
    admin_email: Optional[str] = None
    admin_password: Optional[str] = Field(default=None, min_length=12)

    # Per-rep budget for the LLM endpoints (chat, follow-up suggestions, voice notes).
    rate_limit_llm_per_minute: int = Field(default=20, ge=1)
    # Global budget: AI requests per UTC day across all users and all three endpoints
    # (counted in the database, so restarts don't reset it). 0 disables the cap.
    llm_daily_cap: int = Field(default=500, ge=0)

    # Public demo: user management is read-only (so the demo accounts can't be
    # deactivated, re-roled or have their passwords changed) and the UI shows a banner.
    demo_mode: bool = False
    backend_port: int = 8000  # compose-only (host port); declared so .env stays strict

    # Demo accounts created by `python -m app.demo_seed` (demo-rep / demo-manager / demo-admin).
    demo_rep_password: Optional[str] = Field(default=None, min_length=12)
    demo_manager_password: Optional[str] = Field(default=None, min_length=12)
    demo_admin_password: Optional[str] = Field(default=None, min_length=12)

    model_config = SettingsConfigDict(env_file=ENV_FILE, env_file_encoding="utf-8")

    @model_validator(mode="after")
    def _build_database_url(self) -> "Settings":
        if not self.database_url:
            missing = [
                name
                for name, value in (
                    ("POSTGRES_USER", self.postgres_user),
                    ("POSTGRES_PASSWORD", self.postgres_password),
                    ("POSTGRES_DB", self.postgres_db),
                )
                if not value
            ]
            if missing:
                raise ValueError(f"DATABASE_URL is not set and neither are {', '.join(missing)}")
            self.database_url = (
                f"postgresql+asyncpg://{self.postgres_user}:{self.postgres_password}"
                f"@{self.postgres_host}:{self.postgres_port}/{self.postgres_db}"
            )
        # Hosted providers give postgres://...?sslmode=require; use the async driver
        # and fail now (not on first query) if sslmode is invalid.
        self.database_url = normalize_database_url(self.database_url)
        split_ssl(self.database_url)
        return self

    @property
    def allowed_origins(self) -> list[str]:
        """FRONTEND_ORIGIN plus CORS_ORIGINS, without trailing slashes or duplicates."""
        origins: list[str] = []
        for raw in [self.frontend_origin, *self.cors_origins.split(",")]:
            origin = raw.strip().rstrip("/")
            if origin and origin not in origins:
                origins.append(origin)
        return origins


def load_settings() -> Settings:
    """Build Settings, failing fast with a readable message if anything is missing or unknown."""
    try:
        return Settings(_env_file=ENV_FILE)  # type: ignore[call-arg]  # pydantic-settings init kwarg, not a field
    except ValidationError as exc:
        problems = []
        for err in exc.errors():
            loc = str(err["loc"][0]).upper() if err["loc"] else "DATABASE_URL"
            if err["type"] == "extra_forbidden":
                problems.append(f"{loc} (unknown key)")
            elif err["type"] == "missing" or "at least 1 character" in err["msg"]:
                problems.append(f"{loc} (missing)")
            else:
                problems.append(f"{loc} ({err['msg'].removeprefix('Value error, ')})")
        raise RuntimeError(
            f"Invalid configuration: {'; '.join(sorted(set(problems)))}. "
            f"Settings come from the environment or {ENV_FILE} (see .env.example)."
        ) from exc


settings = load_settings()
