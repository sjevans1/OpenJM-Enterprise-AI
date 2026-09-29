from functools import lru_cache
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict


# Resolve configuration from the repository root, not the process working directory.
# The backend is normally started from ./backend, while .env lives at the repo root.
REPO_ROOT = Path(__file__).resolve().parents[3]
ENV_FILE = REPO_ROOT / ".env"


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=str(ENV_FILE),
        env_prefix="OPENJM_",
        extra="ignore",
    )

    database_url: str = f"sqlite+aiosqlite:///{REPO_ROOT / 'data' / 'openjm.db'}"
    upload_dir: Path = REPO_ROOT / "data" / "uploads"

    model_base_url: str = "http://127.0.0.1:8642/v1"
    model_api_key: str = ""
    model_name: str = "hermes-agent"
    model_timeout_seconds: int = 120

    knowledge_enabled: bool = True
    vector_path: Path = REPO_ROOT / "data" / "vector"
    vector_collection: str = "openjm_default"
    embedding_model: str = "sentence-transformers/all-MiniLM-L6-v2"
    rag_top_k: int = 5
    rag_score_threshold: float = 0.25

    dev_user_id: str = "local-admin"

    def ensure_directories(self) -> None:
        self.upload_dir.mkdir(parents=True, exist_ok=True)
        self.vector_path.mkdir(parents=True, exist_ok=True)
        (REPO_ROOT / "data").mkdir(parents=True, exist_ok=True)


@lru_cache
def get_settings() -> Settings:
    settings = Settings()
    settings.ensure_directories()
    return settings
