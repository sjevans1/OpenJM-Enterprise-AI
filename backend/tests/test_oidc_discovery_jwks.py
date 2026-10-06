"""Discovery-only OIDC configuration, and signing-key selection.

A deployment may configure ``oidc_issuer`` and ``oidc_discovery_url`` and leave
``oidc_jwks_url`` empty; the JWKS location then comes from the provider's
discovery document. That is a valid OIDC deployment mode and has to work.

It used to deadlock. ``jwks()`` held the single client lock and, in that mode,
called ``_jwks_url()`` -> ``discovery()``, which tried to take the same
non-reentrant ``asyncio.Lock``. Every test here that validates a token would
hang rather than fail, so they are all bounded by a timeout: a regression shows
up as a fast, deterministic failure instead of a stuck suite.

The second half covers signing-key selection. A token that does not name the key
that signed it is refused, rather than silently selecting the first published
key.
"""

import asyncio
import base64
import json

import jwt
import pytest
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.asymmetric import padding

from app.core.config import get_settings
from app.services.oidc import OIDCValidationError, oidc_client
from oidc_testkit import AUDIENCE, ISSUER, TestIdP

settings = get_settings()

SUBJECT = "user-discovery"

DISCOVERY_URL = "https://idp.test.invalid/.well-known/openid-configuration"
JWKS_URL = "https://idp.test.invalid/protocol/openid-connect/certs"

# Generous enough to never fire on a healthy machine, short enough that a
# deadlock is reported in seconds rather than hanging the suite.
DEADLOCK_TIMEOUT = 10.0


def _reset_caches() -> None:
    oidc_client._jwks = type(oidc_client._jwks)({})
    oidc_client._discovery = type(oidc_client._discovery)({})


class _Provider:
    """A stubbed provider the client reaches over its real network hop.

    ``fetch`` is what the client calls instead of httpx, and it records how many
    times each document was requested so caching can be asserted.
    """

    def __init__(self, idp: TestIdP):
        self.idp = idp
        self.discovery_calls = 0
        self.jwks_calls = 0
        self.publish_jwks_uri = True
        self.fail_jwks = False

    async def fetch(self, url: str) -> dict:
        if url == DISCOVERY_URL:
            self.discovery_calls += 1
            document = {"issuer": ISSUER, "token_endpoint": f"{ISSUER}/token"}
            if self.publish_jwks_uri:
                document["jwks_uri"] = JWKS_URL
            return document
        if url == JWKS_URL:
            self.jwks_calls += 1
            if self.fail_jwks:
                raise RuntimeError("provider is down")
            return self.idp.jwks()
        raise AssertionError(f"unexpected fetch of {url}")


@pytest.fixture
def idp():
    return TestIdP()


@pytest.fixture
def discovery_only(monkeypatch, idp):
    """OIDC mode with a discovery URL and *no* explicit JWKS URL."""
    provider = _Provider(idp)

    monkeypatch.setattr(settings, "auth_mode", "oidc")
    monkeypatch.setattr(settings, "oidc_issuer", ISSUER)
    monkeypatch.setattr(settings, "oidc_audience", AUDIENCE)
    monkeypatch.setattr(settings, "oidc_discovery_url", DISCOVERY_URL)
    monkeypatch.setattr(settings, "oidc_jwks_url", "")
    monkeypatch.setattr(settings, "oidc_algorithms", "RS256")
    monkeypatch.setattr(settings, "oidc_jwks_cache_seconds", 300)
    monkeypatch.setattr(settings, "oidc_clock_skew_seconds", 30)
    monkeypatch.setattr(settings, "tenant_resolution", "claim")
    monkeypatch.setattr(oidc_client, "settings", settings)
    monkeypatch.setattr(oidc_client, "_fetch_json", provider.fetch)

    _reset_caches()
    return provider


# -- 1. discovery returns jwks_uri, JWKS is fetched, a real token validates ---


async def test_discovery_only_configuration_proves_jwks_uri_and_validates(
    discovery_only, idp
):
    """The whole chain: discovery -> jwks_uri -> JWKS -> a genuine signed token."""
    assert oidc_client.configured, "issuer + discovery URL is a configured deployment"
    assert settings.oidc_jwks_url == "", "this mode deliberately has no explicit JWKS URL"

    # Discovery exposes the JWKS location.
    document = await asyncio.wait_for(oidc_client.discovery(), DEADLOCK_TIMEOUT)
    assert document["jwks_uri"] == JWKS_URL
    assert discovery_only.discovery_calls == 1

    # The JWKS location is resolved from discovery, not from configuration.
    resolved = await asyncio.wait_for(oidc_client._jwks_url(), DEADLOCK_TIMEOUT)
    assert resolved == JWKS_URL
    assert discovery_only.jwks_calls == 0, "resolving the URL must not fetch the keys"

    # The keys are fetched from that location.
    keys = await asyncio.wait_for(oidc_client.jwks(), DEADLOCK_TIMEOUT)
    assert discovery_only.jwks_calls == 1
    assert [key["kid"] for key in keys["keys"]] == [idp.kid]

    # A genuine RS256 token signed by the published key validates.
    token = idp.token(SUBJECT, tenant="acme")
    claims = await oidc_client.validate(token)
    assert claims["sub"] == SUBJECT
    assert claims["iss"] == ISSUER
    assert claims["aud"] == AUDIENCE


async def test_validation_completes_instead_of_deadlocking(discovery_only, idp):
    """The regression itself: this call used to hang forever."""
    token = idp.token(SUBJECT, tenant="acme")
    claims = await asyncio.wait_for(oidc_client.validate(token), DEADLOCK_TIMEOUT)
    assert claims["sub"] == SUBJECT


async def test_concurrent_validations_do_not_deadlock(discovery_only, idp):
    """Several callers racing a cold cache all complete and share the fetches."""
    tokens = [idp.token(f"{SUBJECT}-{index}", tenant="acme") for index in range(5)]
    claims = await asyncio.wait_for(
        asyncio.gather(*(oidc_client.validate(token) for token in tokens)),
        DEADLOCK_TIMEOUT,
    )
    assert {item["sub"] for item in claims} == {f"{SUBJECT}-{index}" for index in range(5)}
    assert discovery_only.discovery_calls == 1, "discovery is coalesced across callers"
    assert discovery_only.jwks_calls == 1, "the keys are fetched once, not per caller"


# -- 5. caching still behaves correctly -------------------------------------


async def test_discovery_and_jwks_are_cached_across_validations(discovery_only, idp):
    await oidc_client.validate(idp.token(SUBJECT, tenant="acme"))
    await oidc_client.validate(idp.token(SUBJECT, tenant="acme"))

    assert discovery_only.discovery_calls == 1
    assert discovery_only.jwks_calls == 1


async def test_force_refresh_refetches_the_keys(discovery_only, idp):
    await asyncio.wait_for(oidc_client.validate(idp.token(SUBJECT, tenant="acme")), DEADLOCK_TIMEOUT)
    assert discovery_only.jwks_calls == 1

    await asyncio.wait_for(oidc_client.jwks(force_refresh=True), DEADLOCK_TIMEOUT)
    assert discovery_only.jwks_calls == 2

    # Refreshing the keys must not re-fetch the still-fresh discovery document.
    assert discovery_only.discovery_calls == 1


async def test_expired_caches_are_refetched(discovery_only, idp, monkeypatch):
    await asyncio.wait_for(oidc_client.validate(idp.token(SUBJECT, tenant="acme")), DEADLOCK_TIMEOUT)
    assert (discovery_only.discovery_calls, discovery_only.jwks_calls) == (1, 1)

    monkeypatch.setattr(settings, "oidc_jwks_cache_seconds", 0)
    await asyncio.wait_for(oidc_client.validate(idp.token(SUBJECT, tenant="acme")), DEADLOCK_TIMEOUT)
    assert (discovery_only.discovery_calls, discovery_only.jwks_calls) == (2, 2)


# -- fail-closed behaviour on the discovery path -----------------------------


async def test_discovery_without_a_jwks_uri_is_refused(discovery_only):
    discovery_only.publish_jwks_uri = False
    with pytest.raises(OIDCValidationError) as caught:
        await asyncio.wait_for(oidc_client.jwks(), DEADLOCK_TIMEOUT)
    assert caught.value.code == "oidc_no_jwks"


async def test_discovery_failure_is_refused(discovery_only, monkeypatch, idp):
    async def boom(url: str) -> dict:
        raise RuntimeError("provider is down")

    monkeypatch.setattr(oidc_client, "_fetch_json", boom)
    with pytest.raises(OIDCValidationError) as caught:
        await asyncio.wait_for(oidc_client.validate(idp.token(SUBJECT, tenant="acme")), DEADLOCK_TIMEOUT)
    assert caught.value.code == "oidc_discovery_failed"


async def test_unreachable_jwks_is_refused(discovery_only, idp):
    discovery_only.fail_jwks = True
    with pytest.raises(OIDCValidationError) as caught:
        await asyncio.wait_for(oidc_client.validate(idp.token(SUBJECT, tenant="acme")), DEADLOCK_TIMEOUT)
    assert caught.value.code == "oidc_jwks_failed"


# -- signing-key selection ---------------------------------------------------


def test_valid_known_kid_selects_that_key(idp):
    key = oidc_client._signing_key(idp.token(SUBJECT), idp.jwks())
    assert key is not None


def test_missing_kid_is_refused(idp):
    """Without a key id there is no basis for choosing a key, so refuse."""
    with pytest.raises(OIDCValidationError) as caught:
        oidc_client._signing_key(idp.missing_kid(SUBJECT), idp.jwks())
    assert caught.value.code == "token_missing_kid"


def test_empty_kid_is_refused(idp):
    token = jwt.encode(
        {"sub": SUBJECT, "iss": ISSUER, "aud": AUDIENCE}, idp._pem(), algorithm="RS256",
        headers={"kid": ""},
    )
    with pytest.raises(OIDCValidationError) as caught:
        oidc_client._signing_key(token, idp.jwks())
    assert caught.value.code == "token_missing_kid"


def _b64url(value: bytes) -> bytes:
    return base64.urlsafe_b64encode(value).rstrip(b"=")


def test_non_string_kid_is_refused(idp):
    """PyJWT will not encode a non-string key id, so build the header by hand.

    The library refuses while parsing the header rather than while choosing a
    key. The important property is that the token is never accepted.
    """
    header = _b64url(json.dumps({"alg": "RS256", "typ": "JWT", "kid": 12345}).encode())
    payload = _b64url(json.dumps({"sub": SUBJECT, "iss": ISSUER, "aud": AUDIENCE}).encode())
    signing_input = header + b"." + payload
    signature = _b64url(idp._private.sign(signing_input, padding.PKCS1v15(), hashes.SHA256()))
    token = (signing_input + b"." + signature).decode()

    with pytest.raises(OIDCValidationError) as caught:
        oidc_client._signing_key(token, idp.jwks())
    assert caught.value.code in {"token_missing_kid", "token_malformed"}


def test_unknown_kid_is_refused(idp):
    with pytest.raises(OIDCValidationError) as caught:
        oidc_client._signing_key(idp.unknown_kid(SUBJECT), idp.jwks())
    assert caught.value.code == "token_unknown_kid"


def test_missing_kid_does_not_select_the_first_published_key(idp):
    """The exact behaviour being removed: a kid-less token must not validate."""
    token = idp.missing_kid(SUBJECT, tenant="acme")
    published = idp.jwks()["keys"][0]
    assert "kid" not in jwt.get_unverified_header(token)
    assert published["kid"] == idp.kid, "a key is published and would have matched"

    with pytest.raises(OIDCValidationError) as caught:
        oidc_client._signing_key(token, idp.jwks())
    assert caught.value.code == "token_missing_kid"


def test_no_keys_published_refuses_a_known_kid(idp):
    with pytest.raises(OIDCValidationError) as caught:
        oidc_client._signing_key(idp.token(SUBJECT), {"keys": []})
    assert caught.value.code == "token_unknown_kid"


async def test_missing_kid_is_refused_end_to_end(discovery_only, idp):
    """Discovery-only mode, real validation path, kid-less token."""
    with pytest.raises(OIDCValidationError) as caught:
        await asyncio.wait_for(
            oidc_client.validate(idp.missing_kid(SUBJECT, tenant="acme")), DEADLOCK_TIMEOUT
        )
    assert caught.value.code == "token_missing_kid"


async def test_discovery_only_jwks_lookup_resolves_and_caches(discovery_only, idp):
    """A caller asking only for the keys exercises discovery then caches both."""
    keys = await asyncio.wait_for(oidc_client.jwks(), DEADLOCK_TIMEOUT)
    again = await asyncio.wait_for(oidc_client.jwks(), DEADLOCK_TIMEOUT)

    assert keys == again
    assert discovery_only.discovery_calls == 1
    assert discovery_only.jwks_calls == 1
