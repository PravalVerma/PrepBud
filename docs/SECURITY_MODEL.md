# Security Model — School in a Box

> **Version:** 0.1.0-draft  
> **Last updated:** 2026-09-17  
> **Status:** Pre-implementation / Architecture Phase

---

## 1. Security Principles

1. **Defence in depth.** No single layer is trusted absolutely.
2. **Least privilege.** Users access only their own data. Services access only what they need.
3. **Data isolation.** Every database query filters by `user_id`. There is no cross-user data access.
4. **Secrets never in code.** All credentials, API keys, and tokens are environment variables.
5. **Audit everything.** Authentication events, data access, and AI interactions are logged.
6. **Fail closed.** If auth verification fails, deny access. If an AI safety check fails, block output.

---

## 2. Authentication Architecture

### 2.1 Supabase Auth Integration

```
┌──────────┐      ┌──────────────┐      ┌────────────┐
│ Frontend │─────▶│ Supabase Auth│─────▶│ JWT Issued │
│ (Next.js)│      │ (hosted)     │      │ (access +  │
└──────────┘      └──────────────┘      │  refresh)  │
                                         └─────┬──────┘
                                               │
                  ┌──────────────┐              │
                  │   FastAPI    │◀─────────────┘
                  │ (JWT verify) │  Authorization: Bearer <jwt>
                  └──────────────┘
```

### 2.2 Authentication Flow

1. **Registration**: Student signs up via Supabase Auth (email/password or OAuth).
2. **Login**: Supabase issues a JWT (access token) and a refresh token.
3. **Token storage**: Access token stored in `httpOnly`, `secure`, `sameSite=strict` cookie. Refresh token in a separate `httpOnly` cookie.
4. **API calls**: Frontend includes the JWT in the `Authorization: Bearer` header (or cookie-based).
5. **Backend verification**: FastAPI middleware verifies the JWT signature using Supabase's public JWKS endpoint.
6. **User sync**: On first API call, if the user doesn't exist in PostgreSQL, create `User` + `StudentProfile` records (keyed by Supabase `sub` claim).

### 2.3 JWT Verification

```python
from jose import jwt, JWTError
from fastapi import Depends, HTTPException, Security
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials

security = HTTPBearer()

async def verify_jwt(
    credentials: HTTPAuthorizationCredentials = Security(security),
) -> dict:
    """Verify Supabase JWT and return claims."""
    try:
        payload = jwt.decode(
            credentials.credentials,
            SUPABASE_JWT_SECRET,  # or JWKS verification
            algorithms=["HS256"],
            audience="authenticated",
        )
        return payload
    except JWTError:
        raise HTTPException(status_code=401, detail="Invalid token")

async def get_current_user(
    claims: dict = Depends(verify_jwt),
) -> User:
    """Resolve JWT claims to a User record."""
    user = await user_repo.get_by_auth_id(claims["sub"])
    if not user:
        user = await user_repo.create_from_auth(claims)
    return user
```

### 2.4 Session Management

| Concern | Approach |
|---|---|
| Access token lifetime | 1 hour (Supabase default) |
| Refresh token lifetime | 7 days |
| Token refresh | Automatic via Supabase client SDK |
| Logout | Revoke refresh token via Supabase; client deletes cookies |
| Concurrent sessions | Allowed (multiple devices) |

---

## 3. Authorization Model

### 3.1 Single-User Data Isolation

In MVP, every user is an independent student. There are no roles, no admin, no shared data.

**Enforcement**: Every database query includes a `WHERE user_id = :current_user_id` clause.

```python
class BaseRepository:
    """All repositories enforce user-scoped queries."""
    
    async def get_by_id(self, id: UUID, user_id: UUID) -> Model | None:
        stmt = select(self.model).where(
            self.model.id == id,
            self.model.user_id == user_id,  # Always filter by user
        )
        return await self.session.scalar(stmt)
    
    async def list(self, user_id: UUID, **filters) -> list[Model]:
        stmt = select(self.model).where(
            self.model.user_id == user_id,  # Always filter by user
        )
        # Apply additional filters...
        return (await self.session.scalars(stmt)).all()
```

### 3.2 Resource Ownership Validation

Before any mutating operation, verify the resource belongs to the current user:

```python
async def update_document(
    document_id: UUID,
    update: DocumentUpdate,
    current_user: User = Depends(get_current_user),
):
    document = await document_repo.get_by_id(document_id, current_user.id)
    if not document:
        raise HTTPException(status_code=404)  # Not 403, to avoid leaking existence
    # Proceed with update...
```

### 3.3 Future Role Model (Post-MVP)

When teachers/institutions are added, the model extends to:

| Role | Access |
|---|---|
| Student | Own data only |
| Teacher | Own data + read access to assigned students' progress |
| Admin | Institutional data management |

This is not implemented in MVP.

---

## 4. Data Security

### 4.1 Data Classification

| Classification | Examples | Storage | Encryption |
|---|---|---|---|
| **Public** | None in MVP | — | — |
| **Internal** | Concept definitions, question templates | PostgreSQL | At rest (managed DB) |
| **Confidential** | Student mastery data, session history, AI interactions | PostgreSQL | At rest + TLS in transit |
| **Sensitive** | Auth credentials, API keys, uploaded documents | Supabase Auth / S3 / env vars | At rest + TLS + access control |

### 4.2 Encryption

| Layer | Mechanism |
|---|---|
| In transit | TLS 1.2+ for all connections (HTTPS, database, Redis, Qdrant, S3) |
| At rest (database) | Managed PostgreSQL encryption (provided by Supabase/Neon) |
| At rest (S3) | Server-side encryption (SSE-S3 or SSE-KMS) |
| At rest (Redis) | TLS-enabled Redis (Upstash/managed) |
| Secrets | Environment variables; never committed to source control |

### 4.3 Data Retention

| Data Type | Retention | Deletion |
|---|---|---|
| User account | Until account deletion | Hard delete on request |
| Uploaded documents | Until user deletes | Remove from S3 + PostgreSQL + Qdrant |
| Session history | Indefinite (student value) | Soft delete on account deletion |
| AI interaction logs | 90 days (configurable) | Automated cleanup job |
| Mastery data | Until account deletion | Hard delete on request |

### 4.4 Account Deletion

On account deletion request:
1. Delete all S3 objects (uploaded documents).
2. Delete all Qdrant vectors (document embeddings).
3. Delete all PostgreSQL records (cascade from User).
4. Delete Supabase Auth account.
5. Log the deletion event (anonymised).

---

## 5. API Security

### 5.1 Rate Limiting

| Endpoint Category | Rate Limit | Window |
|---|---|---|
| Authentication | 10 requests | per minute |
| Document upload | 5 uploads | per hour |
| Learning session start | 10 sessions | per hour |
| Session interaction (WebSocket) | 60 messages | per minute |
| General API | 100 requests | per minute |

Implementation: Redis-based sliding window rate limiter.

### 5.2 Input Validation

- All request bodies validated via Pydantic models.
- File uploads: validate MIME type, file size (max 50MB), file extension whitelist (`.pdf`, `.txt`, `.png`, `.jpg`, `.jpeg`).
- Text inputs: max length enforcement, no script injection.
- UUIDs: validated format.

### 5.3 CORS

```python
app.add_middleware(
    CORSMiddleware,
    allow_origins=[FRONTEND_URL],  # Specific origin, not "*"
    allow_credentials=True,
    allow_methods=["GET", "POST", "PUT", "DELETE", "PATCH"],
    allow_headers=["Authorization", "Content-Type"],
)
```

### 5.4 Security Headers

```python
# Applied via middleware
{
    "X-Content-Type-Options": "nosniff",
    "X-Frame-Options": "DENY",
    "X-XSS-Protection": "1; mode=block",
    "Strict-Transport-Security": "max-age=31536000; includeSubDomains",
    "Content-Security-Policy": "default-src 'self'; ...",
    "Referrer-Policy": "strict-origin-when-cross-origin",
}
```

---

## 6. AI-Specific Security

### 6.1 Prompt Injection Mitigation

| Vector | Mitigation |
|---|---|
| Student input in prompts | Student text is always placed in clearly delimited sections (`---USER INPUT---`). System prompts instruct the model to treat user input as data, not instructions. |
| Uploaded document content | Document content is treated as reference material, not executable instructions. |
| Output validation | AI outputs are validated for format compliance before delivery. |

### 6.2 Content Safety

- System prompts include explicit safety instructions: no harmful content, no personal advice, educational content only.
- Post-generation check: if output contains flagged patterns, block and regenerate.
- Student inputs are not used to fine-tune models.

### 6.3 Cost Safety

- Per-user daily LLM cost budget: $5.00 (configurable).
- Per-session token budget: 50,000 tokens (configurable).
- If budget exceeded: graceful session termination with informative message.
- Alert if any single user's daily cost exceeds threshold.

---

## 7. Infrastructure Security

### 7.1 Secrets Management

| Secret | Storage | Rotation |
|---|---|---|
| Database connection string | Environment variable | On compromise |
| Supabase JWT secret | Environment variable | Via Supabase dashboard |
| LLM API keys | Environment variable | Quarterly or on compromise |
| S3 access keys | Environment variable / IAM role | IAM role preferred |
| Redis connection string | Environment variable | On compromise |
| Qdrant API key | Environment variable | On compromise |

### 7.2 Dependency Security

- Automated dependency vulnerability scanning (Dependabot / Snyk).
- Pin major versions; allow patch updates.
- Regular dependency audit (`pip audit`, `npm audit`).

### 7.3 Logging Security

- Never log: passwords, full JWTs, API keys, full LLM prompts with student PII.
- Always log: user IDs (not names), action types, timestamps, error codes.
- Structured JSON logging with correlation IDs.
- Prompt/response content: store hash in logs, full content in `AIInteraction` table (access-controlled).

---

## 8. Threat Model (Key Threats)

| Threat | Impact | Mitigation |
|---|---|---|
| Credential theft (JWT) | Unauthorised access to student data | Short-lived tokens, httpOnly cookies, token rotation |
| Cross-user data access | Privacy breach | User-scoped queries, no cross-user endpoints |
| Prompt injection | Model manipulation, data exfiltration | Input sanitisation, delimited prompts, output validation |
| File upload abuse | Server compromise, storage abuse | MIME validation, size limits, virus scanning (future) |
| DDoS on AI endpoints | Cost explosion, service degradation | Rate limiting, cost budgets, circuit breakers |
| SQL injection | Data breach | Parameterised queries (SQLAlchemy), input validation |
| Dependency vulnerability | Supply chain attack | Automated scanning, pinned versions |
