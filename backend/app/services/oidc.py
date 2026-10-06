"""OpenJM-owned OIDC token validation.

This is deliberately a *client* implementation: OpenJM validates tokens issued
by an external identity provider (Keycloak, Entra, Okta, ...). It owns its own
discovery cache, JWKS cache and session store, and shares no code, database,
session or secret with any other product.

Validation is fail-closed. Every one of the following denies the request:

* no configured issuer / audience / JWKS source;
* a token that does not name the key that signed it;
* unknown signing key id;
* signature failure;
* wrong issuer, wrong audience, or missing audience;
* expired or not-yet-valid token (beyond a small, configured clock skew);
* missing subject;
* an unsupported algorithm (``none`` is never accepted).

The reference deployment used a Keycloak-style provider. Live interop with a
running Keycloak container is recorded separately in the acceptance evidence;
what this module guarantees is the token-validation contract itself.
"""

from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass, field

import httpx
import jwt

from app.core.config import Settings, get_settings
from app.core.identity import AuthenticationError


@dataclass
class _CacheEntry:
    value: dict = field(default_factory=dict)
    fetched_at: float = 0.0

    def fresh(self, ttl: int) -> bool:
        return bool(self.value) and (time.monotonic() - self.fetched_at) < ttl


class OIDCValidationError(AuthenticationError):
    """A token failed validation. Always maps to HTTP 401."""


class OIDCClient:
    """Validates bearer tokens against a configured OIDC provider."""

    def __init__(self, settings: Settings | None = None) -> None:
        self.settings = settings or get_settings()
        self._discovery = _CacheEntry()
        self._jwks = _CacheEntry()
        # One lock per cache. A lock is never held while another is acquired,
        # and no cache fill re-enters its own lock, so resolving signing keys
        # through discovery cannot deadlock against the discovery cache.
        self._discovery_lock = asyncio.Lock()
        self._jwks_lock = asyncio.Lock()

    # -- configuration ----------------------------------------------------

    @property
    def configured(self) -> bool:
        return bool(
            self.settings.oidc_issuer
            and (self.settings.oidc_jwks_url or self.settings.oidc_discovery_url)
        )

    def _algorithms(self) -> list[str]:
        allowed = [
            item.strip()
            for item in (self.settings.oidc_algorithms or "").split(",")
            if item.strip()
        ]
        # Never allow an unsigned token, even if an operator lists it.
        return [item for item in allowed if item.lower() != "none"]

    # -- remote metadata --------------------------------------------------

    async def _fetch_json(self, url: str) -> dict:
        async with httpx.AsyncClient(timeout=10.0) as client:
            response = await client.get(url)
            response.raise_for_status()
            return response.json()

    async def discovery(self) -> dict:
        if not self.settings.oidc_discovery_url:
            return {}
        async with self._discovery_lock:
            return await self._discovery_locked()

    async def _discovery_locked(self) -> dict:
        """Return the discovery document, fetching it when the cache is stale.

        The caller must already hold ``_discovery_lock``. Keeping the fill in a
        separate, non-locking helper is what makes it safe for ``jwks()`` to
        reach discovery while holding nothing.
        """
        ttl = self.settings.oidc_jwks_cache_seconds
        if self._discovery.fresh(ttl):
            return self._discovery.value
        try:
            document = await self._fetch_json(self.settings.oidc_discovery_url)
        except Exception as exc:  # noqa: BLE001 - fail closed on any failure
            raise OIDCValidationError(
                "OIDC discovery document is unavailable", code="oidc_discovery_failed"
            ) from exc
        self._discovery = _CacheEntry(document, time.monotonic())
        return document

    async def _jwks_url(self) -> str:
        if self.settings.oidc_jwks_url:
            return self.settings.oidc_jwks_url
        document = await self.discovery()
        url = document.get("jwks_uri")
        if not url:
            raise OIDCValidationError(
                "OIDC provider did not publish a JWKS URI", code="oidc_no_jwks"
            )
        return str(url)

    async def jwks(self, *, force_refresh: bool = False) -> dict:
        ttl = self.settings.oidc_jwks_cache_seconds
        if not force_refresh and self._jwks.fresh(ttl):
            return self._jwks.value

        # Resolve the URL *before* taking the JWKS lock. Under discovery-only
        # configuration this reaches discovery(), which takes the discovery
        # lock, so holding the JWKS lock here would be a nested acquisition.
        url = await self._jwks_url()

        async with self._jwks_lock:
            # Another coroutine may have refreshed while this one waited.
            if not force_refresh and self._jwks.fresh(ttl):
                return self._jwks.value
            try:
                document = await self._fetch_json(url)
            except OIDCValidationError:
                raise
            except Exception as exc:  # noqa: BLE001
                raise OIDCValidationError(
                    "OIDC signing keys are unavailable", code="oidc_jwks_failed"
                ) from exc
            self._jwks = _CacheEntry(document, time.monotonic())
            return document

    def _signing_key(self, token: str, jwks: dict):
        try:
            header = jwt.get_unverified_header(token)
        except Exception as exc:  # noqa: BLE001
            raise OIDCValidationError("Token header is malformed", code="token_malformed") from exc
        kid = header.get("kid")
        algorithm = header.get("alg")
        if not algorithm or algorithm.lower() == "none":
            raise OIDCValidationError("Unsigned tokens are not accepted", code="token_unsigned")
        if algorithm not in self._algorithms():
            raise OIDCValidationError(
                f"Token algorithm '{algorithm}' is not accepted", code="token_bad_alg"
            )
        # A token has to name the key that signed it. There is no justified
        # single-key exception, so selecting the first published key when the
        # header carries no key id would be a silent, unverifiable choice.
        if not isinstance(kid, str) or not kid:
            raise OIDCValidationError(
                "Token does not name a signing key", code="token_missing_kid"
            )
        key = next(
            (item for item in (jwks.get("keys") or []) if item.get("kid") == kid), None
        )
        if key is None:
            raise OIDCValidationError("Token signing key is unknown", code="token_unknown_kid")
        try:
            return jwt.algorithms.get_default_algorithms()[algorithm].from_jwk(key)
        except Exception as exc:  # noqa: BLE001
            raise OIDCValidationError(
                "Signing key could not be loaded", code="token_bad_key"
            ) from exc

    # -- validation -------------------------------------------------------

    async def validate(self, token: str) -> dict:
        """Return verified claims, or raise :class:`OIDCValidationError`."""
        if not self.configured:
            raise OIDCValidationError(
                "OIDC is not configured on this deployment", code="oidc_not_configured"
            )
        if not token or token.count(".") != 2:
            raise OIDCValidationError("Token is not a well-formed JWT", code="token_malformed")

        jwks = await self.jwks()
        key = self._signing_key(token, jwks)
        try:
            claims = jwt.decode(
                token,
                key,
                algorithms=self._algorithms(),
                audience=self.settings.oidc_audience or None,
                issuer=self.settings.oidc_issuer,
                options={
                    "require": ["exp", "iat", "sub"],
                    "verify_aud": bool(self.settings.oidc_audience),
                    "verify_iss": True,
                    "verify_exp": True,
                    "verify_nbf": True,
                },
                leeway=self.settings.oidc_clock_skew_seconds,
            )
        except OIDCValidationError:
            raise
        except jwt.ExpiredSignatureError as exc:
            raise OIDCValidationError("Token has expired", code="token_expired") from exc
        except jwt.ImmatureSignatureError as exc:
            raise OIDCValidationError("Token is not yet valid", code="token_not_yet_valid") from exc
        except jwt.InvalidAudienceError as exc:
            raise OIDCValidationError("Token audience is wrong", code="token_bad_audience") from exc
        except jwt.InvalidIssuerError as exc:
            raise OIDCValidationError("Token issuer is wrong", code="token_bad_issuer") from exc
        except jwt.InvalidSignatureError as exc:
            raise OIDCValidationError("Token signature is invalid", code="token_bad_signature") from exc
        except Exception as exc:  # noqa: BLE001
            raise OIDCValidationError("Token failed validation", code="token_invalid") from exc

        if not claims.get("sub"):
            raise OIDCValidationError("Token has no subject", code="token_no_subject")
        return claims

    def claims_to_identity(self, claims: dict) -> dict:
        """Extract the OpenJM-relevant identity fields from verified claims."""
        email = claims.get("email") or claims.get("preferred_username")
        display = claims.get("name") or claims.get("preferred_username") or email
        tenant = claims.get(self.settings.oidc_tenant_claim)
        return {
            "subject": str(claims["sub"]),
            "issuer": claims.get("iss") or self.settings.oidc_issuer,
            "email": str(email) if email else None,
            "display_name": str(display) if display else None,
            "tenant_hint": str(tenant) if tenant else None,
        }


oidc_client = OIDCClient()
