"""A minimal OIDC provider used by the VS5 acceptance tests.

It issues real RS256-signed tokens and publishes a real JWKS document, so the
production validator in ``app.services.oidc`` is exercised end to end rather
than mocked. What it does not do is run a Keycloak container: live interop with
a specific provider image is recorded separately in the acceptance evidence.

The helper can also mint deliberately broken tokens (expired, wrong audience,
wrong issuer, unknown key, unsigned) so the fail-closed paths are tested for
real.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field

import jwt
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa

ISSUER = "https://idp.test.invalid/realms/openjm"
AUDIENCE = "openjm-enterprise-ai"
KID = "test-key-1"


def _new_key():
    return rsa.generate_private_key(public_exponent=65537, key_size=2048)


@dataclass
class TestIdP:
    # Not a test class: pytest must not try to collect it.
    __test__ = False
    issuer: str = ISSUER
    audience: str = AUDIENCE
    kid: str = KID
    _private: object = field(default=None, repr=False)
    _other: object = field(default=None, repr=False)

    def __post_init__(self) -> None:
        self._private = _new_key()
        self._other = _new_key()

    # -- keys --------------------------------------------------------------

    def jwks(self) -> dict:
        public = self._private.public_key().public_numbers()
        return {
            "keys": [
                {
                    "kty": "RSA",
                    "kid": self.kid,
                    "use": "sig",
                    "alg": "RS256",
                    "n": jwt.utils.to_base64url_uint(public.n).decode(),
                    "e": jwt.utils.to_base64url_uint(public.e).decode(),
                }
            ]
        }

    def _pem(self, key=None) -> str:
        return (
            (key or self._private)
            .private_bytes(
                encoding=serialization.Encoding.PEM,
                format=serialization.PrivateFormat.PKCS8,
                encryption_algorithm=serialization.NoEncryption(),
            )
            .decode()
        )

    # -- tokens ------------------------------------------------------------

    def token(
        self,
        subject: str,
        *,
        tenant: str | None = None,
        email: str | None = None,
        name: str | None = None,
        issuer: str | None = None,
        audience: str | None = None,
        expires_in: int = 600,
        not_before: int | None = None,
        key: str = "private",
        kid: str | None = None,
        algorithm: str = "RS256",
        extra: dict | None = None,
    ) -> str:
        now = int(time.time())
        claims = {
            "sub": subject,
            "iss": issuer or self.issuer,
            "aud": audience or self.audience,
            "iat": now,
            "exp": now + expires_in,
            "email": email or f"{subject}@example.test",
            "name": name or subject,
        }
        if tenant:
            claims["tenant"] = tenant
        if not_before is not None:
            claims["nbf"] = not_before
        if extra:
            claims.update(extra)
        signing_key = self._pem() if key == "private" else self._pem(self._other)
        headers = {"kid": kid or self.kid}
        if algorithm == "none":
            return jwt.encode(claims, key="", algorithm="none", headers=headers)
        return jwt.encode(claims, signing_key, algorithm=algorithm, headers=headers)

    def expired(self, subject: str, **kwargs) -> str:
        return self.token(subject, expires_in=-3600, **kwargs)

    def not_yet_valid(self, subject: str, **kwargs) -> str:
        return self.token(subject, not_before=int(time.time()) + 3600, **kwargs)

    def wrong_audience(self, subject: str, **kwargs) -> str:
        return self.token(subject, audience="some-other-app", **kwargs)

    def wrong_issuer(self, subject: str, **kwargs) -> str:
        return self.token(subject, issuer="https://evil.invalid/realms/openjm", **kwargs)

    def foreign_key(self, subject: str, **kwargs) -> str:
        """Signed by a key the published JWKS does not contain."""
        return self.token(subject, key="other", **kwargs)

    def unknown_kid(self, subject: str, **kwargs) -> str:
        return self.token(subject, kid="not-published", **kwargs)

    def unsigned(self, subject: str, **kwargs) -> str:
        return self.token(subject, algorithm="none", **kwargs)

    def garbage(self) -> str:
        return "not.a.jwt"

    def opaque(self) -> str:
        return "opaque-token-with-no-dots"
