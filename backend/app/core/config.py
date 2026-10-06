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
    # Bounded-retry settings for malformed model output (Phase B reliability).
    # Default extras enable the Gemma 4 chat-template thinking channel, the
    # established root-cause fix for the llama-server deployment; override
    # via OPENJM_MODEL_RETRY_REQUEST_EXTRAS for a different runtime, or "{}"
    # to disable.
    model_retry_temperature: float = 0.0
    model_retry_max_tokens: int = 512
    model_retry_request_extras: str = '{"chat_template_kwargs": {"enable_thinking": true}}'

    knowledge_enabled: bool = True
    vector_path: Path = REPO_ROOT / "data" / "vector"
    vector_collection: str = "openjm_default"
    embedding_model: str = "sentence-transformers/all-MiniLM-L6-v2"
    rag_top_k: int = 5
    rag_score_threshold: float = 0.25
    # Phase E keeps semantic top-k intact and adds at most two exact ±1 chunks
    # around the two strongest primary matches. Final evidence is therefore
    # bounded by rag_top_k + rag_neighbor_max_chunks.
    rag_neighbor_primary_limit: int = 2
    rag_neighbor_max_chunks: int = 2

    credential_encryption_key: str = ""
    credential_key_file: Path = REPO_ROOT / "data" / "credentials.key"
    structured_max_rows: int = 200
    structured_timeout_seconds: int = 20

    dev_user_id: str = "local-admin"
    # VS4-B2C2 Phase 5: default-off release gate for report-run execution.
    # The POST /runs endpoint returns 424 when False. Enable only in
    # isolated acceptance via OPENJM_REPORT_RUNS_ENABLED=true.
    report_runs_enabled: bool = False

    # --- VS5 trusted identity -------------------------------------------------
    # 'oidc' requires a validated credential on every request and is the
    # production default. 'dev' resolves a single local principal that is
    # provisioned as a real tenant membership; it exists for local development
    # and the offline test suite only, and never bypasses authorization checks.
    auth_mode: str = "dev"
    auth_allow_dev_mode: bool = True

    # OIDC / SSO. Values are supplied by the operator; OpenJM owns the client,
    # session and authorization code and never shares state with Workspace.
    oidc_issuer: str = ""
    oidc_audience: str = ""
    oidc_jwks_url: str = ""
    oidc_discovery_url: str = ""
    oidc_jwks_cache_seconds: int = 300
    oidc_clock_skew_seconds: int = 60
    oidc_algorithms: str = "RS256,ES256"
    # Maps a validated token to a tenant. 'claim' reads the configured claim
    # (which may be a slug or an OpenJM tenant id); 'single_membership' accepts
    # the principal's only active membership and refuses when ambiguous.
    oidc_tenant_claim: str = "tenant"
    tenant_resolution: str = "claim"

    session_ttl_seconds: int = 28800
    # Login is opt-in: without these the OIDC endpoints refuse to start a flow
    # rather than falling back to an unauthenticated principal.
    oidc_client_id: str = ""
    oidc_client_secret: str = ""
    oidc_redirect_uri: str = "http://127.0.0.1:5173/auth/callback"

    # --- VS6 bounded action runtime ------------------------------------------
    actions_enabled: bool = True
    action_max_steps: int = 6
    action_budget_seconds: int = 60
    action_plan_ttl_seconds: int = 900
    action_approval_ttl_seconds: int = 900

    # --- #6 document lifecycle ------------------------------------------------
    # A lease bounds how long one process may hold a document. An expired lease
    # is reclaimable, which is what makes crash recovery deterministic.
    document_lease_seconds: int = 120

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
