"""JWT verification (SECURITY_MODEL §2.3) — HS256 and JWKS (ES256) paths."""

from __future__ import annotations

from typing import Any

import jwt
import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa

from app.core.exceptions import AuthenticationError
from app.core.security import JWKSCache, JWTVerifier
from tests.support import (
    EC_PRIVATE_KEY,
    OTHER_EC_PRIVATE_KEY,
    TEST_KID,
    build_settings,
    jwks,
    make_claims,
    make_token,
)


class FakeFetcher:
    def __init__(self, payload: dict[str, Any] | Exception | None = None) -> None:
        self.payload = payload if payload is not None else jwks()
        self.calls = 0

    async def __call__(self, url: str) -> dict[str, Any]:
        self.calls += 1
        if isinstance(self.payload, Exception):
            raise self.payload
        return self.payload


def make_verifier(fetcher: FakeFetcher | None = None, **settings: Any) -> JWTVerifier:
    s = build_settings(**settings)
    cache = JWKSCache(s.jwks_url, s.jwks_cache_ttl_seconds, fetcher=fetcher or FakeFetcher())
    return JWTVerifier(s, jwks_cache=cache)


class TestHS256:
    async def test_valid_token_returns_claims(self) -> None:
        claims = await make_verifier().verify(make_token("user-1", "u@example.com"))
        assert claims["sub"] == "user-1"
        assert claims["email"] == "u@example.com"

    @pytest.mark.parametrize(
        ("kwargs", "message"),
        [
            ({"exp_delta": -60}, "Token has expired"),
            ({"aud": "anon"}, "Invalid token"),
            ({"iss": "https://other.supabase.co/auth/v1"}, "Invalid token"),
            ({"secret": "wrong-secret-wrong-secret-wrong-secret"}, "Invalid token"),
        ],
    )
    async def test_rejections(self, kwargs: dict[str, Any], message: str) -> None:
        with pytest.raises(AuthenticationError, match=message):
            await make_verifier().verify(make_token(**kwargs))

    async def test_within_leeway_still_valid(self) -> None:
        await make_verifier().verify(make_token(exp_delta=-5))

    async def test_missing_exp_rejected(self) -> None:
        claims = make_claims()
        del claims["exp"]
        token = jwt.encode(claims, build_settings().supabase_jwt_secret.get_secret_value())
        with pytest.raises(AuthenticationError):
            await make_verifier().verify(token)

    async def test_empty_sub_rejected(self) -> None:
        with pytest.raises(AuthenticationError):
            await make_verifier().verify(make_token(sub=""))

    async def test_issuer_not_checked_without_supabase_url(self) -> None:
        verifier = make_verifier(supabase_url="")
        await verifier.verify(make_token(iss="anything"))

    async def test_hs256_disabled_without_secret(self) -> None:
        with pytest.raises(AuthenticationError):
            await make_verifier(supabase_jwt_secret="").verify(make_token())


class TestJWKS:
    async def test_es256_valid(self) -> None:
        claims = await make_verifier().verify(make_token("es", alg="ES256"))
        assert claims["sub"] == "es"

    async def test_keys_are_cached(self) -> None:
        fetcher = FakeFetcher()
        verifier = make_verifier(fetcher)
        for _ in range(3):
            await verifier.verify(make_token(alg="ES256"))
        assert fetcher.calls == 1

    async def test_unknown_kid_triggers_one_refetch_then_fails(self) -> None:
        fetcher = FakeFetcher()
        verifier = make_verifier(fetcher)
        await verifier.verify(make_token(alg="ES256"))
        with pytest.raises(AuthenticationError):
            await verifier.verify(make_token(alg="ES256", kid="rotated"))
        assert fetcher.calls == 2

    async def test_key_rotation_picked_up(self) -> None:
        fetcher = FakeFetcher()
        verifier = make_verifier(fetcher)
        await verifier.verify(make_token(alg="ES256"))
        fetcher.payload = jwks((TEST_KID, EC_PRIVATE_KEY), ("new-kid", OTHER_EC_PRIVATE_KEY))

        claims = await verifier.verify(
            make_token("rotated", alg="ES256", kid="new-kid", private_key=OTHER_EC_PRIVATE_KEY)
        )

        assert claims["sub"] == "rotated"

    async def test_expired_cache_refetches(self) -> None:
        fetcher = FakeFetcher()
        verifier = make_verifier(fetcher, jwks_cache_ttl_seconds=-1)
        await verifier.verify(make_token(alg="ES256"))
        await verifier.verify(make_token(alg="ES256"))
        assert fetcher.calls == 2

    async def test_signature_from_wrong_key_rejected(self) -> None:
        with pytest.raises(AuthenticationError):
            await make_verifier().verify(make_token(alg="ES256", private_key=OTHER_EC_PRIVATE_KEY))

    async def test_fetch_failure_fails_closed(self) -> None:
        verifier = make_verifier(FakeFetcher(RuntimeError("network down")))
        with pytest.raises(AuthenticationError, match="Unable to verify token"):
            await verifier.verify(make_token(alg="ES256"))

    async def test_missing_kid_rejected(self) -> None:
        with pytest.raises(AuthenticationError):
            await make_verifier().verify(make_token(alg="ES256", kid=None))

    async def test_asymmetric_rejected_when_jwks_not_configured(self) -> None:
        verifier = JWTVerifier(build_settings(supabase_url="", supabase_jwks_url=""))
        assert verifier.jwks_cache is None
        with pytest.raises(AuthenticationError):
            await verifier.verify(make_token(alg="ES256"))

    async def test_alg_mismatch_with_key_rejected(self) -> None:
        """A token claiming RS256 must not be checked against an ES256 key."""
        rsa_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
        token = jwt.encode(make_claims(), rsa_key, algorithm="RS256", headers={"kid": TEST_KID})
        with pytest.raises(AuthenticationError):
            await make_verifier().verify(token)

    async def test_hs256_signed_with_public_key_rejected(self) -> None:
        """Classic alg-confusion: HS256 'signed' with the public key bytes."""
        import base64
        import hashlib
        import hmac
        import json

        def b64(data: bytes) -> str:
            return base64.urlsafe_b64encode(data).rstrip(b"=").decode()

        public_pem = EC_PRIVATE_KEY.public_key().public_bytes(
            serialization.Encoding.PEM, serialization.PublicFormat.SubjectPublicKeyInfo
        )
        # PyJWT refuses to build this token, so forge it by hand.
        header = b64(json.dumps({"alg": "HS256", "typ": "JWT", "kid": TEST_KID}).encode())
        payload = b64(json.dumps(make_claims()).encode())
        signing_input = f"{header}.{payload}".encode()
        signature = b64(hmac.new(public_pem, signing_input, hashlib.sha256).digest())
        token = f"{header}.{payload}.{signature}"
        with pytest.raises(AuthenticationError):
            await make_verifier().verify(token)


@pytest.mark.parametrize("token", ["", "abc", "a.b.c", "eyJhbGciOiJub25lIn0.e30."])
async def test_garbage_tokens_rejected(token: str) -> None:
    with pytest.raises(AuthenticationError):
        await make_verifier().verify(token)


async def test_unsigned_token_rejected() -> None:
    token = jwt.encode(make_claims(), key=None, algorithm="none")
    with pytest.raises(AuthenticationError):
        await make_verifier().verify(token)


async def test_default_fetcher_is_http(monkeypatch: pytest.MonkeyPatch) -> None:
    """The production fetcher performs a GET against the JWKS URL."""
    import httpx

    from app.core import security

    seen: dict[str, str] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["url"] = str(request.url)
        return httpx.Response(200, json=jwks())

    real_client = httpx.AsyncClient

    def client_factory(*args: Any, **kwargs: Any) -> httpx.AsyncClient:
        return real_client(transport=httpx.MockTransport(handler))

    monkeypatch.setattr(security.httpx, "AsyncClient", client_factory)

    data = await security.fetch_jwks_http("https://x.supabase.co/auth/v1/.well-known/jwks.json")

    assert seen["url"].endswith("/.well-known/jwks.json")
    assert data["keys"][0]["kid"] == TEST_KID
