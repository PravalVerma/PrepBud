"""Supabase Auth JWT verification (SECURITY_MODEL §2.3).

Two signing modes are supported, selected by the token's ``alg`` header:

* **Asymmetric (RS256 / ES256 / EdDSA)** — current Supabase projects. The public
  key is looked up by ``kid`` in the project's JWKS endpoint (cached, refreshed
  once on an unknown ``kid`` to follow key rotation).
* **HS256** — legacy Supabase projects, verified with ``SUPABASE_JWT_SECRET``.

A mode is only accepted if it is configured, and the verification algorithm is
pinned to the key's own algorithm, preventing alg-confusion attacks. Every
failure raises `AuthenticationError` (fail closed).
"""

from __future__ import annotations

import asyncio
import time
from collections.abc import Awaitable, Callable
from typing import Any

import httpx
import jwt
from jwt import PyJWK, PyJWKSet

from app.config import Settings
from app.core.exceptions import AuthenticationError
from app.core.logging import get_logger

logger = get_logger(__name__)

ASYMMETRIC_ALGORITHMS = frozenset({"RS256", "ES256", "EdDSA"})
REQUIRED_CLAIMS = ["exp", "sub", "aud"]

JWKSFetcher = Callable[[str], Awaitable[dict[str, Any]]]


async def fetch_jwks_http(url: str) -> dict[str, Any]:
    async with httpx.AsyncClient(timeout=5.0) as client:
        response = await client.get(url)
        response.raise_for_status()
        data: dict[str, Any] = response.json()
        return data


class JWKSCache:
    """Caches the JWKS key set with a TTL; refetches on unknown key IDs."""

    def __init__(self, url: str, ttl_seconds: int, fetcher: JWKSFetcher = fetch_jwks_http) -> None:
        self.url = url
        self.ttl_seconds = ttl_seconds
        self._fetcher = fetcher
        self._keys: dict[str, PyJWK] = {}
        self._fetched_at = 0.0
        self._lock = asyncio.Lock()

    def _expired(self) -> bool:
        return (time.monotonic() - self._fetched_at) > self.ttl_seconds

    async def _refresh(self) -> None:
        raw = await self._fetcher(self.url)
        key_set = PyJWKSet.from_dict(raw)
        self._keys = {k.key_id: k for k in key_set.keys if k.key_id}
        self._fetched_at = time.monotonic()

    async def get_key(self, kid: str) -> PyJWK:
        async with self._lock:
            if not self._keys or self._expired() or kid not in self._keys:
                try:
                    await self._refresh()
                except Exception as exc:  # network / parse failures → fail closed
                    logger.warning("JWKS fetch failed", extra={"error": type(exc).__name__})
                    raise AuthenticationError("Unable to verify token") from exc
        key = self._keys.get(kid)
        if key is None:
            raise AuthenticationError("Invalid token")
        return key


class JWTVerifier:
    def __init__(self, settings: Settings, jwks_cache: JWKSCache | None = None) -> None:
        self.audience = settings.supabase_jwt_audience
        self.issuer = settings.jwt_issuer
        self.leeway = settings.jwt_leeway_seconds
        self._hs_secret = settings.supabase_jwt_secret.get_secret_value()
        if jwks_cache is None and settings.jwks_url:
            jwks_cache = JWKSCache(settings.jwks_url, settings.jwks_cache_ttl_seconds)
        self.jwks_cache = jwks_cache

    async def verify(self, token: str) -> dict[str, Any]:
        try:
            header = jwt.get_unverified_header(token)
        except jwt.PyJWTError as exc:
            raise AuthenticationError("Invalid token") from exc

        alg = header.get("alg")
        key: Any
        if alg == "HS256":
            if not self._hs_secret:
                raise AuthenticationError("Invalid token")
            key = self._hs_secret
        elif alg in ASYMMETRIC_ALGORITHMS:
            kid = header.get("kid")
            if self.jwks_cache is None or not isinstance(kid, str):
                raise AuthenticationError("Invalid token")
            jwk = await self.jwks_cache.get_key(kid)
            if jwk.algorithm_name != alg:
                raise AuthenticationError("Invalid token")
            key = jwk.key
        else:
            raise AuthenticationError("Invalid token")

        try:
            claims: dict[str, Any] = jwt.decode(
                token,
                key=key,
                algorithms=[alg],
                audience=self.audience,
                issuer=self.issuer,
                leeway=self.leeway,
                options={"require": REQUIRED_CLAIMS},
            )
        except jwt.ExpiredSignatureError as exc:
            raise AuthenticationError("Token has expired") from exc
        except jwt.PyJWTError as exc:
            raise AuthenticationError("Invalid token") from exc

        if not isinstance(claims.get("sub"), str) or not claims["sub"]:
            raise AuthenticationError("Invalid token")
        return claims
