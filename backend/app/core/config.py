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

    # --- BV5-A chat artifacts -------------------------------------------------
    # Downloadable work products created by General Chat. Storage lives under the
    # upload directory so it is covered by the existing backup/restore set; keys
    # are opaque and server-generated, never a caller-supplied path.
    artifacts_dir: Path = REPO_ROOT / "data" / "uploads" / "artifacts"
    # Bounded artifact size and an explicit MIME allow-list. A format whose
    # resolved MIME is not listed is refused, never coerced to another type.
    max_artifact_bytes: int = 2_000_000
    artifact_allowed_mime_types: str = "text/html,text/markdown,text/plain,text/csv"

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

    # --- VS8 deployment profile, release and white-label metadata -------------
    # 'development' is the local/CI default and is never a production claim.
    # 'production' is a supported, hardened profile; startup performs a strict
    # fail-closed configuration preflight (app.core.preflight).
    deployment_profile: str = "development"
    # Build/release identifier surfaced in /api/version and logs. A release
    # artefact sets this; a developer checkout leaves it empty.
    release_id: str = ""

    # White-label configuration. Values are display metadata only: they are
    # rendered as text (never as raw HTML) by the frontend, so they cannot
    # become an injection surface. Security-sensitive names are not settable.
    product_name: str = "OpenJM Enterprise AI"
    organization_name: str = ""
    brand_logo_url: str = ""
    browser_page_title: str = ""
    support_contact: str = ""
    theme_accent: str = ""

    # --- VS8 model-provider routing -------------------------------------------
    # 'local' = an OpenAI-compatible endpoint reachable on the local/on-prem
    # network (may be plain HTTP). 'private_remote' = an operator-managed private
    # OpenAI-compatible service; production requires HTTPS/TLS. Routing is
    # backend-only: the credential never reaches the browser (see model_gateway).
    model_provider_mode: str = "local"
    # Retained, explicit, development/local-only escape hatch for insecure HTTP.
    # Production refuses plain HTTP to a private_remote endpoint.
    model_allow_insecure_http: bool = True
    # Fail-closed by design: OpenJM never silently routes to an unapproved
    # public provider. 'none' means a provider failure surfaces as an error.
    model_provider_fallback: str = "none"

    # --- VS8 production security surface --------------------------------------
    # Comma-separated explicit origins. A production profile refuses '*'.
    cors_origins: str = "http://localhost:5173,http://127.0.0.1:5173"
    # Comma-separated host allow-list. A production profile refuses '*'.
    trusted_hosts: str = "*"
    # When True, honour X-Forwarded-* from a configured reverse proxy boundary.
    trust_proxy_headers: bool = False
    security_headers_enabled: bool = True
    # Request-hardening bounds. 413 on an over-size body / upload.
    max_request_body_bytes: int = 8_000_000
    max_upload_bytes: int = 25_000_000
    # Bounded rate limits for expensive endpoints (per client window). Off by
    # default (development); production preflight requires it enabled.
    rate_limit_enabled: bool = False
    rate_limit_expensive_per_minute: int = 60

    # --- VS8 observability ----------------------------------------------------
    metrics_enabled: bool = True
    # Shared bearer credential for the detailed operational surface (Prometheus
    # metrics and the readiness detail report). Empty in development, where the
    # endpoints stay open for local/CI convenience; a production profile hides
    # them unless this is set. Never returned by any endpoint.
    ops_token: str = ""
    # The scheduler runs as an in-process bounded tick when enabled. Default off
    # so the offline suite and a single-shot deployment are unaffected.
    scheduler_enabled: bool = False
    scheduler_tick_seconds: int = 30
    scheduler_tick_limit: int = 20

    # --- VS8 backup and retention ---------------------------------------------
    backup_dir: Path = REPO_ROOT / "data" / "backups"
    # Retention is opt-in and bounded; destructive classes support dry-run and
    # are tenant-scoped. Audit evidence, customer source data, report history
    # and regulatory records are never eligible for automatic deletion.
    retention_enabled: bool = False
    retention_sessions_days: int = 30
    retention_scheduler_runs_days: int = 30
    retention_notification_history_days: int = 90
    retention_connector_run_history_days: int = 30

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

    @field_validator(
        "upload_dir",
        "artifacts_dir",
        "vector_path",
        "credential_key_file",
        "backup_dir",
        mode="after",
    )
    @classmethod
    def resolve_repo_relative_paths(cls, value: Path) -> Path:
        if value.is_absolute():
            return value
        return (REPO_ROOT / value).resolve()

    def ensure_directories(self) -> None:
        self.upload_dir.mkdir(parents=True, exist_ok=True)
        self.artifacts_dir.mkdir(parents=True, exist_ok=True)
        self.vector_path.mkdir(parents=True, exist_ok=True)
        self.credential_key_file.parent.mkdir(parents=True, exist_ok=True)
        self.backup_dir.mkdir(parents=True, exist_ok=True)
        (REPO_ROOT / "data").mkdir(parents=True, exist_ok=True)

    @property
    def is_production(self) -> bool:
        return self.deployment_profile.strip().lower() == "production"

    @property
    def cors_origin_list(self) -> list[str]:
        return [item.strip() for item in self.cors_origins.split(",") if item.strip()]

    @property
    def trusted_host_list(self) -> list[str]:
        return [item.strip() for item in self.trusted_hosts.split(",") if item.strip()]

    @property
    def artifact_allowed_mime_list(self) -> list[str]:
        """The artifact MIME allow-list (lower-cased, comma-separated setting)."""
        return [
            item.strip().lower()
            for item in self.artifact_allowed_mime_types.split(",")
            if item.strip()
        ]


@lru_cache
def get_settings() -> Settings:
    settings = Settings()
    settings.ensure_directories()
    return settings
