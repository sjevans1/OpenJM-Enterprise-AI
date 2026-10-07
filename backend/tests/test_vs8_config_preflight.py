"""VS8 Workstream C/I: production configuration preflight fails closed.

These tests are hermetic about the profile they assert: they build an explicit
``Settings`` object rather than reading the developer's gitignored ``.env``, so
the same commit passes on a developer machine and in CI.
"""

from __future__ import annotations

from cryptography.fernet import Fernet

from app.core.config import Settings
from app.core.preflight import (
    Classification,
    CONFIGURATION_SURFACE,
    assert_configuration_or_raise,
    validate_configuration,
)


def _production_settings(**overrides) -> Settings:
    """A deliberately *safe* production configuration, then overridden per test."""
    base = dict(
        deployment_profile="production",
        auth_mode="oidc",
        auth_allow_dev_mode=False,
        oidc_issuer="https://idp.example.com",
        oidc_client_id="openjm",
        oidc_client_secret="s3cret-value-not-a-placeholder",
        oidc_redirect_uri="https://openjm.example.com/auth/callback",
        database_url="postgresql+asyncpg://openjm:pw@db:5432/openjm",
        credential_encryption_key=Fernet.generate_key().decode(),
        cors_origins="https://openjm.example.com",
        trusted_hosts="openjm.example.com",
        model_provider_mode="private_remote",
        model_base_url="https://llm.internal.example.com/v1",
        model_name="openjm-private-8b",
        model_api_key="provider-key-not-a-placeholder",
        model_provider_fallback="none",
        rate_limit_enabled=True,
    )
    base.update(overrides)
    return Settings(**base)


def test_development_profile_is_valid_and_honest() -> None:
    report = validate_configuration(Settings(deployment_profile="development"))
    assert report.ok
    # Development must not masquerade as production.
    assert any("development profile" in w.message for w in report.warnings)


def test_production_safe_configuration_passes() -> None:
    report = validate_configuration(_production_settings())
    assert report.ok, report.render()


def test_production_rejects_dev_auth() -> None:
    report = validate_configuration(_production_settings(auth_mode="dev"))
    assert not report.ok
    assert any(i.setting == "auth_mode" for i in report.errors)


def test_production_requires_oidc_client_secret() -> None:
    report = validate_configuration(_production_settings(oidc_client_secret=""))
    assert any(i.setting == "oidc_client_secret" for i in report.errors)


def test_production_rejects_placeholder_secret() -> None:
    report = validate_configuration(_production_settings(oidc_client_secret="changeme"))
    assert any(i.setting == "oidc_client_secret" for i in report.errors)


def test_production_requires_postgres() -> None:
    report = validate_configuration(
        _production_settings(database_url="sqlite+aiosqlite:///./data/openjm.db")
    )
    assert any(i.setting == "database_url" for i in report.errors)


def test_production_rejects_wildcard_origin_and_host() -> None:
    report = validate_configuration(
        _production_settings(cors_origins="*", trusted_hosts="*")
    )
    assert any(i.setting == "cors_origins" for i in report.errors)
    assert any(i.setting == "trusted_hosts" for i in report.errors)


def test_production_requires_https_for_private_remote() -> None:
    report = validate_configuration(
        _production_settings(model_base_url="http://llm.internal.example.com/v1")
    )
    assert any(i.setting == "model_base_url" for i in report.errors)


def test_production_requires_provider_credential_for_private_remote() -> None:
    report = validate_configuration(_production_settings(model_api_key=""))
    assert any(i.setting == "model_api_key" for i in report.errors)


def test_production_rejects_public_fallback_policy() -> None:
    report = validate_configuration(
        _production_settings(model_provider_fallback="public")
    )
    assert any(i.setting == "model_provider_fallback" for i in report.errors)


def test_production_requires_rate_limiting() -> None:
    report = validate_configuration(_production_settings(rate_limit_enabled=False))
    assert any(i.setting == "rate_limit_enabled" for i in report.errors)


def test_production_requires_vault_key_material(tmp_path) -> None:
    missing = tmp_path / "absent.key"
    report = validate_configuration(
        _production_settings(
            credential_encryption_key="", credential_key_file=missing
        )
    )
    assert any(i.setting == "credential_encryption_key" for i in report.errors)


def test_assert_raises_on_unsafe_configuration() -> None:
    import pytest

    from app.core.preflight import ConfigurationError

    with pytest.raises(ConfigurationError):
        assert_configuration_or_raise(_production_settings(auth_mode="dev"))


def test_report_never_echoes_secret_values() -> None:
    secret = "super-secret-value-xyz"
    report = validate_configuration(
        _production_settings(oidc_client_secret="", model_api_key=secret)
    )
    rendered = report.render()
    assert secret not in rendered


def test_configuration_surface_classifies_secrets() -> None:
    by_name = {f.name: f for f in CONFIGURATION_SURFACE}
    assert by_name["model_api_key"].classification is Classification.SECRET
    assert by_name["oidc_client_secret"].classification is Classification.SECRET
    assert by_name["credential_encryption_key"].classification is Classification.SECRET
