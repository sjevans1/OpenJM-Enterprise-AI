from functools import lru_cache
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_prefix="OPENJM_",
        extra="ignore",
    )

    database_url: str = "sqlite+aiosqlite:///./data/openjm.db"
    upload_dir: Path = Path("./data/uploads")

    model_base_url: str = "http://127.0.0.1:8642/v1"
    model_api_key: str = ""
    model_name: str = "hermes-agent"
    model_timeout_seconds: int = 120

    knowledge_enabled: bool = True
    vector_path: Path = Path("./data/vector")
    vector_collection: str = "openjm_default"
    embedding_model: str = "sentence-transformers/all-MiniLM-L6-v2"
    rag_top_k: int = 5
    rag_score_threshold: float = 0.25

    dev_user_id: str = "local-admin"

    def ensure_directories(self) -> None:
        self.upload_dir.mkdir(parents=True, exist_ok=True)
        self.vector_path.mkdir(parents=True, exist_ok=True)
        Path("./data").mkdir(parents=True, exist_ok=True)


@lru_cache
def get_settings() -> Settings:
    settings = Settings()
    settings.ensure_directories()
    return settings
