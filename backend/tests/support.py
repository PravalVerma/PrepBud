"""Test helpers: settings builder and Supabase-style JWT minting (HS256 + ES256/JWKS)."""

from __future__ import annotations

import time
from typing import Any

import jwt
from cryptography.hazmat.primitives.asymmetric import ec
from jwt.algorithms import ECAlgorithm

from app.config import Settings

TEST_JWT_SECRET = "test-jwt-secret-with-at-least-32-characters!"
TEST_SUPABASE_URL = "https://test-project.supabase.co"
TEST_ISSUER = f"{TEST_SUPABASE_URL}/auth/v1"
TEST_AUDIENCE = "authenticated"
TEST_KID = "test-es256-key"
TEST_BUCKET = "siab-test-uploads"
FAKE_TASKS = (
    "tutor_explanation",
    "question_generation",
    "answer_evaluation",
    "misconception_detection",
    "concept_extraction",
    "session_summary",
)

EC_PRIVATE_KEY = ec.generate_private_key(ec.SECP256R1())
OTHER_EC_PRIVATE_KEY = ec.generate_private_key(ec.SECP256R1())


def build_settings(**overrides: Any) -> Settings:
    values: dict[str, Any] = {
        "app_env": "test",
        "log_level": "WARNING",
        "supabase_url": TEST_SUPABASE_URL,
        "supabase_jwt_secret": TEST_JWT_SECRET,
        "rate_limit_enabled": True,
        "rate_limit_uploads_per_hour": 1000,
        "qdrant_url": "",
        "s3_bucket": TEST_BUCKET,
        "s3_access_key_id": "testing",
        "s3_secret_access_key": "testing",
        # Every AI task goes to the in-process fake provider (tests/fakes.py).
        "llm_tasks": {task: {"provider": "fake", "model": f"fake-{task}"} for task in FAKE_TASKS}
        | {"embedding": {"provider": "fake", "model": "fake-embedding", "dimensions": 64}},
        "ai_pricing": {
            f"fake-{task}": {"input_per_1m": 1.0, "output_per_1m": 2.0} for task in FAKE_TASKS
        }
        | {"fake-embedding": {"input_per_1m": 0.5}},
    }
    values.update(overrides)
    return Settings(_env_file=None, **values)  # type: ignore[call-arg]


def jwks(*keys: tuple[str, ec.EllipticCurvePrivateKey]) -> dict[str, Any]:
    keys = keys or ((TEST_KID, EC_PRIVATE_KEY),)
    out = []
    for kid, private in keys:
        jwk = ECAlgorithm.to_jwk(private.public_key(), as_dict=True)
        jwk.update({"kid": kid, "alg": "ES256", "use": "sig"})
        out.append(jwk)
    return {"keys": out}


def make_claims(
    sub: str = "auth-user-a",
    email: str | None = "student-a@example.com",
    *,
    # Long-lived: modules build headers at import time and the suite runs for minutes.
    exp_delta: int = 86_400,
    aud: str | None = TEST_AUDIENCE,
    iss: str | None = TEST_ISSUER,
    **extra: Any,
) -> dict[str, Any]:
    now = int(time.time())
    claims: dict[str, Any] = {
        "sub": sub,
        "iat": now,
        "exp": now + exp_delta,
        "role": "authenticated",
    }
    if email is not None:
        claims["email"] = email
    if aud is not None:
        claims["aud"] = aud
    if iss is not None:
        claims["iss"] = iss
    claims.update(extra)
    return claims


def make_token(
    sub: str = "auth-user-a",
    email: str | None = "student-a@example.com",
    *,
    alg: str = "HS256",
    secret: str = TEST_JWT_SECRET,
    private_key: ec.EllipticCurvePrivateKey = EC_PRIVATE_KEY,
    kid: str | None = TEST_KID,
    **claim_overrides: Any,
) -> str:
    claims = make_claims(sub, email, **claim_overrides)
    if alg == "HS256":
        return jwt.encode(claims, secret, algorithm="HS256")
    headers = {"kid": kid} if kid else {}
    return jwt.encode(claims, private_key, algorithm=alg, headers=headers)


def auth(sub: str = "auth-user-a", email: str | None = None, **kwargs: Any) -> dict[str, str]:
    email = email if email is not None else f"{sub}@example.com"
    return {"Authorization": f"Bearer {make_token(sub, email, **kwargs)}"}
