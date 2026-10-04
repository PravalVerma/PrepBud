# School in a Box

An AI-powered adaptive learning platform: upload your study material, and a
personal tutor builds a concept graph from it, tracks your mastery per concept,
teaches, quizzes and schedules spaced-repetition reviews.

Architecture, contracts and the phase plan live in [`docs/`](docs/) — start with
[`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md) and
[`docs/DEVELOPMENT_PHASES.md`](docs/DEVELOPMENT_PHASES.md).

**Status:** Phase 2 (Foundation & Data Layer) — auth, full database schema,
curriculum CRUD API, app shell.

## Repository layout

```text
backend/    FastAPI + SQLAlchemy 2.0 (async) + Alembic   → backend/README.md
frontend/   Next.js 16 (App Router) + React Query + Zustand → frontend/README.md
docs/       Product, architecture, data model, API contract, test strategy
docker-compose.yml        Local PostgreSQL 16 + Redis 7
docker-compose.test.yml   Disposable test databases (tmpfs)
.github/workflows/test.yml  CI: lint, type check, tests, build, E2E
```

## Prerequisites

- Docker (Desktop) with Compose v2
- [uv](https://docs.astral.sh/uv/) (installs Python 3.12 for the backend)
- Node.js ≥ 20.9 (CI uses 24)
- A Supabase project (Auth only) for real sign-in — not needed for the test suites

## Quick start

```bash
cp .env.example .env                    # backend + compose settings
cp .env.example frontend/.env.local     # frontend reads SUPABASE_URL/ANON_KEY/API_URL/SITE_URL
# fill in SUPABASE_URL, SUPABASE_ANON_KEY and SUPABASE_JWT_SECRET (legacy HS256 projects only)

make up          # docker compose up -d --wait   (PostgreSQL + Redis)
make install     # uv sync + npm install
make migrate     # alembic upgrade head
make dev-backend # http://localhost:8000/docs  (health: /health)
make dev-frontend# http://localhost:3000
```

No `make` (e.g. Windows)? Each target in the [`Makefile`](Makefile) is a one-line
command you can run directly, e.g. `cd backend && uv run alembic upgrade head`.

If ports 5432/6379 are taken, set `POSTGRES_PORT`/`REDIS_PORT` in `.env` and
update `DATABASE_URL`/`REDIS_URL` to match.

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
