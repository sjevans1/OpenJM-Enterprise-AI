"""VS8 production configuration preflight.

One authoritative place decides whether a configuration is *safe to operate*,
separate from whether the process happens to import. Two consumers:

* startup (:func:`app.main.lifespan`) calls :func:`assert_configuration_or_raise`
  so a production deployment with an unsafe combination refuses to serve; and
* the operator command (``python -m app.core.preflight``) prints the same report
  before a deployment is started.

Design rules:

* **Fail closed.** A production profile with dev identity, a missing OIDC
  client, a wildcard origin/host, a committed default secret, a plain-HTTP
  private model endpoint or a missing credential key is a hard error, not a
  warning.
* **Never echo secrets.** The report names a setting and a category; it never
  includes the value of a secret-classified setting.
* **Environment-independent.** The development default (the CI and local case)
  is valid by construction so the offline test suite and developer start are
  unchanged. Only an explicit ``deployment_profile=production`` opt-in applies
  the strict rules, so a machine with a local ``.env`` and CI agree.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum

from app.core.config import Settings, get_settings


class Classification(str, Enum):
    """How a configuration value must be treated (VS8 Workstream C)."""

    REQUIRED = "required"
    OPTIONAL = "optional"
    DEVELOPMENT_ONLY = "development-only"
    SECRET = "secret"
    RESTART_REQUIRED = "restart-required"
    SAFE_DEFAULT = "safe-default"


@dataclass(frozen=True)
class ConfigurationField:
    name: str
    classification: Classification
    description: str


# The documented configuration surface. Kept next to the validator so the two
# cannot drift; the docs render this list.
CONFIGURATION_SURFACE: tuple[ConfigurationField, ...] = (
    ConfigurationField("deployment_profile", Classification.REQUIRED, "development | production profile selector."),
    ConfigurationField("auth_mode", Classification.REQUIRED, "Identity mode; 'oidc' is required in production."),
    ConfigurationField("oidc_issuer", Classification.REQUIRED, "OIDC issuer URL."),
    ConfigurationField("oidc_client_id", Classification.REQUIRED, "OIDC client id."),
    ConfigurationField("oidc_client_secret", Classification.SECRET, "OIDC client secret."),
    ConfigurationField("oidc_redirect_uri", Classification.REQUIRED, "OIDC redirect URI."),
    ConfigurationField("database_url", Classification.REQUIRED, "Application-metadata database URL."),
    ConfigurationField("credential_encryption_key", Classification.SECRET, "Fernet key material for the credential vault."),
    ConfigurationField("credential_key_file", Classification.RESTART_REQUIRED, "Fallback key-material file path."),
    ConfigurationField("upload_dir", Classification.RESTART_REQUIRED, "Persistent upload directory."),
    ConfigurationField("vector_path", Classification.RESTART_REQUIRED, "Persistent vector/Knowledge directory."),
    ConfigurationField("backup_dir", Classification.RESTART_REQUIRED, "Backup destination directory."),
    ConfigurationField("session_ttl_seconds", Classification.SAFE_DEFAULT, "Session lifetime."),
    ConfigurationField("cors_origins", Classification.REQUIRED, "Explicit CORS origin allow-list."),
    ConfigurationField("trusted_hosts", Classification.REQUIRED, "Explicit host allow-list."),
    ConfigurationField("trust_proxy_headers", Classification.REQUIRED, "Honour X-Forwarded-* behind a trusted proxy."),
    ConfigurationField("model_provider_mode", Classification.REQUIRED, "local | private_remote model provider."),
    ConfigurationField("model_base_url", Classification.REQUIRED, "Backend-only model endpoint base URL."),
    ConfigurationField("model_name", Classification.REQUIRED, "Model identifier."),
    ConfigurationField("model_api_key", Classification.SECRET, "Backend-only model provider credential."),
    ConfigurationField("model_allow_insecure_http", Classification.DEVELOPMENT_ONLY, "Permit plain HTTP model endpoint."),
    ConfigurationField("model_provider_fallback", Classification.REQUIRED, "Fallback policy; production must be 'none'."),
    ConfigurationField("model_timeout_seconds", Classification.SAFE_DEFAULT, "Bounded provider timeout."),
    ConfigurationField("max_request_body_bytes", Classification.SAFE_DEFAULT, "Request body bound."),
    ConfigurationField("max_upload_bytes", Classification.SAFE_DEFAULT, "Upload bound."),
    ConfigurationField("rate_limit_enabled", Classification.SAFE_DEFAULT, "Expensive-endpoint rate limiting."),
    ConfigurationField("retention_enabled", Classification.OPTIONAL, "Enable bounded retention lifecycle."),
    ConfigurationField("metrics_enabled", Classification.SAFE_DEFAULT, "Expose Prometheus-style metrics."),
    ConfigurationField("ops_token", Classification.SECRET, "Bearer shared by the detailed operational endpoints (metrics, readiness detail)."),
    ConfigurationField("product_name", Classification.OPTIONAL, "White-label display name (text only)."),
    ConfigurationField("organization_name", Classification.OPTIONAL, "White-label organization name."),
    ConfigurationField("release_id", Classification.OPTIONAL, "Build/release identifier."),
)


# Values that are obviously a stock placeholder. A production deployment that
# still carries one has exported a sample config, not provisioned a secret.
_PLACEHOLDER_SECRETS = {
    "changeme",
    "change-me",
    "change_me",
    "example",
    "example-secret",
    "placeholder",
    "secret",
    "password",
    "your-secret-here",
    "<secret>",
    "replace-me",
}
_DEFAULT_DEV_USER_ID = "local-admin"


@dataclass
class PreflightIssue:
    setting: str
    category: str
    message: str

    def __str__(self) -> str:  # pragma: no cover - trivial
        return f"[{self.category}] {self.setting}: {self.message}"


@dataclass
class PreflightReport:
    profile: str
    errors: list[PreflightIssue] = field(default_factory=list)
    warnings: list[PreflightIssue] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.errors

    def add_error(self, setting: str, message: str) -> None:
        self.errors.append(PreflightIssue(setting, "error", message))

    def add_warning(self, setting: str, message: str) -> None:
        self.warnings.append(PreflightIssue(setting, "warning", message))

    def render(self) -> str:
        lines = [f"configuration profile: {self.profile}"]
        if not self.errors and not self.warnings:
            lines.append("OK: configuration is valid for this profile.")
        for issue in self.errors + self.warnings:
            lines.append(f"  {issue}")
        lines.append("RESULT: " + ("VALID" if self.ok else "INVALID (fail closed)"))
        return "\n".join(lines)


def _is_placeholder(value: str) -> bool:
    return value.strip().lower() in _PLACEHOLDER_SECRETS


def _is_insecure_http(url: str) -> bool:
    return url.strip().lower().startswith("http://")


def validate_configuration(settings: Settings) -> PreflightReport:
    """Return a fail-closed validation report for *settings*.

    Development (the default, and the CI/test case) validates structurally.
    Production additionally enforces the hardening contract.
    """
    profile = (settings.deployment_profile or "development").strip().lower()
    report = PreflightReport(profile=profile)

    dev_capable = profile not in {"production", "prod"}

    # ---- model endpoint (both profiles) ----------------------------------
    base_url = (settings.model_base_url or "").strip()
    if not base_url:
        report.add_error("model_base_url", "a model endpoint base URL is required")
    if not (settings.model_name or "").strip():
        report.add_error("model_name", "a model identifier is required")
    if (settings.model_provider_mode or "").strip() not in {"local", "private_remote"}:
        report.add_error(
            "model_provider_mode", "must be 'local' or 'private_remote'"
        )

    if dev_capable:
        # Development is allowed to be plain HTTP and to omit credentials, but
        # the report says so explicitly rather than pretending it is production.
        report.add_warning(
            "deployment_profile",
            "development profile: not a production claim; dev identity and "
            "insecure defaults may be in use",
        )
        if settings.auth_mode == "dev":
            report.add_warning(
                "auth_mode", "dev identity is enabled (development only)"
            )
        return report

    # ---- production-only hardening ---------------------------------------
    if settings.auth_mode != "oidc":
        report.add_error(
            "auth_mode",
            "production requires auth_mode='oidc'; dev identity must not be "
            "usable in production",
        )
    if settings.auth_allow_dev_mode:
        report.add_warning(
            "auth_allow_dev_mode",
            "dev-mode fallback is enabled; disable it in production",
        )

    # OIDC provider configuration.
    for name in ("oidc_issuer", "oidc_client_id", "oidc_client_secret", "oidc_redirect_uri"):
        if not (getattr(settings, name) or "").strip():
            report.add_error(name, "required in production")
    if not (settings.oidc_discovery_url or settings.oidc_jwks_url or settings.oidc_issuer):
        report.add_error(
            "oidc_discovery_url",
            "production requires a discovery URL, a JWKS URL, or an issuer",
        )
    if _is_placeholder(settings.oidc_client_secret):
        report.add_error("oidc_client_secret", "production secret is a placeholder")

    # Credential-vault key material.
    has_inline_key = bool((settings.credential_encryption_key or "").strip())
    has_key_file = settings.credential_key_file.exists()
    if not has_inline_key and not has_key_file:
        report.add_error(
            "credential_encryption_key",
            "production requires vault key material (inline key or key file)",
        )
    if has_inline_key and _is_placeholder(settings.credential_encryption_key):
        report.add_error(
            "credential_encryption_key", "production key material is a placeholder"
        )

    # PostgreSQL metadata database.
    if not settings.database_url.startswith("postgresql"):
        report.add_error(
            "database_url",
            "production requires a PostgreSQL application-metadata database",
        )
    if _DEFAULT_DEV_USER_ID and settings.dev_user_id == _DEFAULT_DEV_USER_ID:
        report.add_warning(
            "dev_user_id", "default dev principal id retained in production"
        )

    # CORS and host policy.
    origins = settings.cors_origin_list
    if not origins:
        report.add_error("cors_origins", "production requires an explicit origin allow-list")
    if "*" in origins:
        report.add_error(
            "cors_origins", "production must not allow the wildcard origin"
        )
    hosts = settings.trusted_host_list
    if not hosts or "*" in hosts:
        report.add_error(
            "trusted_hosts",
            "production requires an explicit host allow-list (no wildcard)",
        )

    # Private model endpoint transport and routing policy.
    if settings.model_provider_mode == "private_remote":
        if _is_insecure_http(base_url):
            report.add_error(
                "model_base_url",
                "a private_remote endpoint must use HTTPS in production "
                "(plain HTTP is development/local-only)",
            )
        if not (settings.model_api_key or "").strip():
            report.add_error(
                "model_api_key",
                "a private_remote provider requires a server-side credential",
            )
        if _is_placeholder(settings.model_api_key):
            report.add_error(
                "model_api_key", "provider credential is a placeholder value"
            )

    if settings.model_provider_fallback not in {"none"}:
        report.add_error(
            "model_provider_fallback",
            "production must fail closed ('none'); no silent public fallback",
        )

    if not settings.rate_limit_enabled:
        report.add_error(
            "rate_limit_enabled",
            "production requires rate limiting on expensive endpoints",
        )

    # Detailed operational endpoints are the trusted monitoring surface. Without
    # a shared token they stay hidden (404) rather than exposing operational
    # state to an unauthenticated caller.
    if not (settings.ops_token or "").strip():
        report.add_warning(
            "ops_token",
            "no ops_token set: /api/metrics and /api/ready/detail stay hidden "
            "in production (set OPENJM_OPS_TOKEN to expose them to monitoring)",
        )

    return report


class ConfigurationError(RuntimeError):
    """Raised at startup when the configuration is unsafe for its profile."""

    def __init__(self, report: PreflightReport):
        self.report = report
        super().__init__(
            "Unsafe configuration for profile "
            f"'{report.profile}':\n{report.render()}"
        )


def assert_configuration_or_raise(settings: Settings | None = None) -> PreflightReport:
    """Validate and raise :class:`ConfigurationError` on any error.

    Returns the (valid) report so the caller can log warnings.
    """
    report = validate_configuration(settings or get_settings())
    if not report.ok:
        raise ConfigurationError(report)
    return report


def _main() -> int:  # pragma: no cover - exercised via subprocess in tests
    import argparse
    import sys

    parser = argparse.ArgumentParser(
        description="Validate the OpenJM configuration for its deployment profile."
    )
    parser.add_argument(
        "--json", action="store_true", help="emit a machine-readable report"
    )
    args = parser.parse_args()

    report = validate_configuration(get_settings())
    if args.json:
        import json

        print(
            json.dumps(
                {
                    "profile": report.profile,
                    "ok": report.ok,
                    "errors": [i.__dict__ for i in report.errors],
                    "warnings": [i.__dict__ for i in report.warnings],
                },
                indent=2,
            )
        )
    else:
        print(report.render())
    return 0 if report.ok else 1


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(_main())
