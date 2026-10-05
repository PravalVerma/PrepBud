# School in a Box

An AI-powered adaptive learning platform: upload your study material, and a
personal tutor builds a concept graph from it, tracks your mastery per concept,
teaches, quizzes and schedules spaced-repetition reviews.

Architecture, contracts and the phase plan live in [`docs/`](docs/) — start with
[`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md) and
[`docs/DEVELOPMENT_PHASES.md`](docs/DEVELOPMENT_PHASES.md).

**Status:** Phase 3 (Content Pipeline). Upload study material (PDF, text or
photos); a background worker extracts the text (with OCR where needed), chunks it,
builds a concept graph with an LLM, and indexes it for hybrid semantic and keyword
search. Phase 2 (auth, schema, curriculum API, app shell) is complete.

## Repository layout

```text
backend/    FastAPI + SQLAlchemy 2.0 (async) + Alembic   → backend/README.md
frontend/   Next.js 16 (App Router) + React Query + Zustand → frontend/README.md
docs/       Product, architecture, data model, API contract, test strategy
docker-compose.yml        Local PostgreSQL 16, Redis 7, Qdrant, S3 (SeaweedFS)
infra/                    Local service config (SeaweedFS S3 credentials)
docker-compose.test.yml   Disposable test databases (tmpfs)
.github/workflows/test.yml  CI: lint, type check, tests, build, E2E
```

## Prerequisites

- Docker (Desktop) with Compose v2
- [uv](https://docs.astral.sh/uv/) (installs Python 3.12 for the backend)
- Node.js ≥ 20.9 (CI uses 24)
- A Supabase project (Auth only) for real sign-in — not needed for the test suites
- An OpenAI-compatible LLM endpoint for document processing: an OpenAI API key, or e.g.
  a local Ollama (`LLM_BASE_URL=http://localhost:11434/v1`). Not needed for the tests.
- Optional: [Tesseract](https://github.com/tesseract-ocr/tesseract) for OCR of scanned
  pages and photos (`apt install tesseract-ocr` / `brew install tesseract` / UB-Mannheim
  installer on Windows). Text PDFs and `.txt` files work without it.

## Quick start

```bash
cp .env.example .env                    # backend + compose settings
cp .env.example frontend/.env.local     # frontend reads SUPABASE_URL/ANON_KEY/API_URL/SITE_URL
# fill in SUPABASE_URL, SUPABASE_ANON_KEY and SUPABASE_JWT_SECRET (legacy HS256 projects only)

make up          # docker compose up -d --wait   (PostgreSQL, Redis, Qdrant, S3)
make install     # uv sync + npm install
make migrate     # alembic upgrade head
make dev-backend # http://localhost:8000/docs  (health: /health); BACKEND_PORT=8001 to change
make dev-worker  # Celery worker that processes uploaded documents
make dev-frontend# http://localhost:3000
```

No `make` (e.g. Windows)? Each target in the [`Makefile`](Makefile) is a one-line
command you can run directly, e.g. `cd backend && uv run alembic upgrade head`.

If ports 5432/6379/6333/8333 are taken, set `POSTGRES_PORT`/`REDIS_PORT`/`QDRANT_PORT`/`S3_PORT`
in `.env` and update `DATABASE_URL`/`REDIS_URL`/`QDRANT_URL`/`S3_ENDPOINT_URL` to match.
If the frontend runs on another origin, add it to `FRONTEND_ORIGINS` (S3 CORS for direct uploads).

### Supabase settings

| Setting | Where | Notes |
|---|---|---|
| `SUPABASE_URL` | backend + frontend | Backend derives the JWKS URL and expected issuer from it |
| `SUPABASE_ANON_KEY` | frontend | Used server-side only |
| `SUPABASE_JWT_SECRET` | backend | Only for projects still on legacy HS256 signing |
| Redirect URLs | Supabase dashboard → Auth | Add `http://localhost:3000/auth/confirm` |

## Tests

```bash
make test        # backend (unit + integration via testcontainers) + frontend (vitest)
make test-e2e    # Playwright, full stack: Next.js → FastAPI → PostgreSQL, mock Supabase Auth
make check       # lint + type check + tests, as CI runs them
```

Backend integration tests start throwaway PostgreSQL/Redis containers
automatically; alternatively point them at `docker-compose.test.yml` with
`TEST_DATABASE_URL` / `TEST_REDIS_URL` (see that file's header).

## How auth works (SECURITY_MODEL §2)

1. Sign-in / sign-up run as **Next.js server actions** against Supabase Auth.
2. The session is stored only in **`httpOnly`, `SameSite=Strict`** cookies
   (`secure` in production) and refreshed by `src/proxy.ts`.
3. The browser calls the API through the same-origin **`/api/backend/*`** route,
   which attaches `Authorization: Bearer <access token>` server-side.
4. FastAPI verifies the JWT (JWKS for ES256/RS256, or the HS256 secret) and, on
   the first call, creates the `User` + `StudentProfile` rows.
5. Every query is scoped to the caller; other users' resources return **404**.

## Content pipeline (Phase 3)

```text
browser ──presigned PUT──▶ S3          POST /documents/upload-url → PUT → POST /documents/{id}/confirm-upload
                            │
Celery worker ◀── Redis ────┘  extract text (pdfplumber; Tesseract OCR for scanned pages/images)
   │                           → semantic chunks (500–1000 tokens, 100 overlap, tables kept whole)
   │                           → concepts per batch of chunks (LLM, concurrent, cached)
   │                           → de-duplication + prerequisite/related edges (LLM, no cycles)
   ├──▶ PostgreSQL             documents, sections, concepts, relationships, links, ai_interactions
   └──▶ Qdrant                 section embeddings (payload filtered by user_id)
```

- **Search:** `GET /api/v1/search?q=…` merges Qdrant vector search with PostgreSQL full-text
  search (reciprocal-rank fusion). It degrades to keyword-only if Qdrant is unavailable.
- **Retries:** the task is idempotent. Retried attempts reuse cached LLM results and replace
  sections instead of duplicating them.
- **Auditing:** every LLM and embedding call is stored in `ai_interactions` with tokens, cost,
  latency, prompt version and content. A per-user daily budget is enforced (`AI_DAILY_BUDGET_USD`).
- **Configuration:** models and providers are set per task in `.env`; see the AI section of
  `.env.example`. Any OpenAI-compatible endpoint works.
