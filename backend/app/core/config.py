from functools import lru_cache
from pathlib import Path

from pydantic import field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


# Resolve configuration from the repository root, not the process working directory.
# The backend is normally started from ./backend, while .env lives at the repo root.
REPO_ROOT = Path(__file__).resolve().parents[3]
ENV_FILE = REPO_ROOT / ".env"


def _sqlite_url_for(path: Path) -> str:
    resolved = path.resolve()
    return f"sqlite+aiosqlite:///{resolved.as_posix()}"


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=str(ENV_FILE),
        env_prefix="OPENJM_",
        extra="ignore",
    )

    database_url: str = _sqlite_url_for(REPO_ROOT / "data" / "openjm.db")
    upload_dir: Path = REPO_ROOT / "data" / "uploads"

    model_base_url: str = "http://127.0.0.1:18080/v1"
    model_api_key: str = ""
    model_name: str = "gemma-4-12b-local"
    model_timeout_seconds: int = 300

    knowledge_enabled: bool = True
    vector_path: Path = REPO_ROOT / "data" / "vector"
    vector_collection: str = "openjm_default"
    embedding_model: str = "sentence-transformers/all-MiniLM-L6-v2"
    rag_top_k: int = 5
    rag_score_threshold: float = 0.25

    credential_encryption_key: str = ""
    credential_key_file: Path = REPO_ROOT / "data" / "credentials.key"
    structured_max_rows: int = 200
    structured_timeout_seconds: int = 20

    dev_user_id: str = "local-admin"

    @field_validator("database_url", mode="after")
    @classmethod
    def resolve_relative_sqlite_url(cls, value: str) -> str:
        """Anchor repo-local SQLite URLs even when .env uses ./data/..."""
        prefixes = (
            "sqlite+aiosqlite:///./",
            "sqlite:///./",
        )
        for prefix in prefixes:
            if value.startswith(prefix):
                relative = value[len(prefix):]
                resolved = (REPO_ROOT / relative).resolve().as_posix()
                scheme = prefix.split(":///")[0]
                return f"{scheme}:///{resolved}"
        return value

    @field_validator("upload_dir", "vector_path", "credential_key_file", mode="after")
    @classmethod
    def resolve_repo_relative_paths(cls, value: Path) -> Path:
        if value.is_absolute():
            return value
        return (REPO_ROOT / value).resolve()

    def ensure_directories(self) -> None:
        self.upload_dir.mkdir(parents=True, exist_ok=True)
        self.vector_path.mkdir(parents=True, exist_ok=True)
        self.credential_key_file.parent.mkdir(parents=True, exist_ok=True)
        (REPO_ROOT / "data").mkdir(parents=True, exist_ok=True)


@lru_cache
def get_settings() -> Settings:
    settings = Settings()
    settings.ensure_directories()
    return settings
