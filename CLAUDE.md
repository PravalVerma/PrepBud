# CLAUDE.md — School in a Box (repo: PrepBud)

AI adaptive learning platform. Built **phase by phase** from the docs in `docs/`, which are the
single source of truth. Read this file first, then the doc sections relevant to the task.

## Where we are

| Phase | Status |
| --- | --- |
| 1 Architecture & contracts (`docs/`, `walkthrough.md`) | ✅ committed |
| 2 Foundation & data layer | ✅ committed (`53602a2`) |
| 3 Content pipeline | ✅ implemented, all AC-3.x verified locally — **awaiting user review/commit** |
| 4–8 | not started — **ask the user before starting each new phase** |

Each phase's deliverables + acceptance criteria (AC-x.y) live in `docs/DEVELOPMENT_PHASES.md`.
At the end of a phase: report AC status, update this table, stop. Never commit/push unless asked
(the user commits; end commit messages with the Co-Authored-By line from the system prompt).

## Non-negotiables (from the original brief)

- Schema = `docs/DATA_MODEL.md` exactly (no new tables/columns without asking). Envelopes, error
  codes, pagination = `docs/API_CONTRACT.md`. Layout = repo structure in `DEVELOPMENT_PHASES.md`.
- Tech stack is fixed (FastAPI, SQLAlchemy 2 async, Alembic, Celery+Redis, Qdrant, S3, Supabase
  Auth, LangGraph, Next.js App Router, React Query, Zustand, Tailwind).
- No hard-coded LLM provider/model (per-task config) · no hard-coded curriculum · concept is the
  atomic unit · session state recoverable · every AI call logged (`ai_traces`/`ai_interactions`)
  · background tasks idempotent · **every query filtered by user_id; other users' resources → 404,
  never 403**.
- Tests as you go; backend coverage gate 80% (`fail_under` in `backend/pyproject.toml`).

## Layout (what exists)

```text
backend/   FastAPI app (uv, Python 3.12)
  app/config.py            Settings (pydantic-settings; nested env delimiter "__")
  app/core/                security (JWT/JWKS), exceptions (error envelope), middleware, logging
  app/api/                 routers; deps.py = DbSession, CurrentUser, PageParams, rate limits
  app/domain/              Pydantic schemas; common.py = Envelope/PaginatedEnvelope/envelope()
  app/db/models/           all 23 tables; app/db/repositories/ (BaseRepository = user-scoped)
  app/ai/                  LLM client, providers, prompts/*.md (Jinja2), cost tracking  [Phase 3]
  app/services/content/    extraction, chunking, concept extraction, retrieval         [Phase 3]
  app/workers/             Celery app + tasks                                          [Phase 3]
  app/integrations/        redis, s3, qdrant
  alembic/versions/        hand-written DDL (one statement per execute — asyncpg)
  tests/unit, tests/integration (testcontainers or TEST_DATABASE_URL/TEST_REDIS_URL), tests/ai
frontend/  Next.js 16 (App Router, React 19, Tailwind v4, TanStack Query 5, Zustand 5)
  src/proxy.ts             (Next 16 replacement for middleware) session refresh + route guard
  src/app/api/backend/[...path]/route.ts   BFF: attaches Bearer token server-side
  src/lib/api.ts           browser API client (unwraps envelopes); src/hooks/, src/types/
  src/app/upload, src/app/concepts(/[id]); src/lib/upload.ts (presigned PUT w/ progress)
  tests/ (vitest), tests/e2e (Playwright: mock Supabase Auth :54329, mock LLM :54330,
          API :8002, worker on Celery queue "e2e", web :3100)
docs/      the spec — PRODUCT_REQUIREMENTS, ARCHITECTURE(+_DECISIONS), DOMAIN_MODEL, DATA_MODEL,
           API_CONTRACT, AI_SYSTEM_DESIGN, LEARNING_ENGINE, SECURITY_MODEL, TEST_STRATEGY, PHASES
```

## Design decisions already made (keep consistent)

- **Auth/BFF:** browser never holds Supabase tokens. Server actions sign in; session cookies are
  httpOnly + SameSite=Strict; the Next route `/api/backend/*` forwards with the Bearer token.
  ⇒ Phase 5 WebSockets need a server-issued short-lived ticket (browser can't send the JWT).
- Backend JWT: JWKS (ES256/RS256/EdDSA, alg pinned to key) + optional legacy HS256 secret. The
  user's Supabase project signs **ES256** (no JWT secret needed). User+profile auto-provisioned on
  first authenticated call (race-safe upsert).
- Envelope: `{data, meta:{request_id,timestamp}}`; lists add `meta.pagination`; errors
  `{error:{code,message,details}, meta}`; FastAPI validation errors → 400 `VALIDATION_ERROR`.
- Frontend env vars are runtime server-only (`SUPABASE_URL`, `SUPABASE_ANON_KEY`, `API_URL`,
  `SITE_URL`) — **not** `NEXT_PUBLIC_*`.
- Timezones: Chromium may report legacy IANA names (Asia/Calcutta) → mapped in profile form.

### Phase 3 decisions

- Documents upload direct-to-S3 via presigned PUT; `confirm-upload` HEADs the object (size/type),
  sets `processing`, enqueues Celery `process_document`. Re-confirm while processing is idempotent;
  on `failed` it re-queues; on `ready` → 409.
- Pipeline (`services/content/document_processor.py`): extract (pdfplumber; Tesseract OCR fallback
  for text-less pages/images; TXT) → chunk (500–1000 tok, 100 overlap, tables/code/equations kept
  intact) → concept extraction (LLM, batched + concurrent, results cached in Redis per chunk-batch
  hash) → relationships + LLM-assisted dedup → one DB transaction → embed + Qdrant upsert →
  `ready`. Section ids are uuid5(document_id, index) ⇒ retries overwrite, never duplicate.
  Prerequisite edges that would create a cycle are dropped. Qdrant failure ⇒ doc still `ready`,
  `processing_metadata.embedding_status="failed"`, re-index task retried; search degrades to
  keyword. Edge direction: `source` **is a prerequisite of** `target`.
- LLM: OpenAI-compatible HTTP (`httpx`), providers + per-task models from config
  (`LLM_PROVIDERS`, `LLM_TASKS__<task>__MODEL=...` merged onto defaults). Every call → `AIInteraction`
  (hashes + full prompt/response in `metadata`, cost from `AI_PRICING`), grouped by `AITrace`;
  per-user daily budget in Redis `cost:{user_id}:{date}`.
- `GET /search` (hybrid vector + Postgres FTS, reciprocal-rank fusion) was **added** to
  API_CONTRACT in Phase 3 (contract had no search endpoint; needed for AC-3.4).
- Deleting a document removes S3 object, Qdrant points, sections, and *orphaned extracted*
  concepts that have no learning data (mastery/questions).
- Celery queue name is configurable (`CELERY_QUEUE`, default `default`) so E2E/dev stacks can share
  one Redis without stealing each other's tasks.
- Product assumptions resolved with the documented defaults: concept granularity "assessable,
  teachable in 2–5 min"; no copyright tracking; no pre-built concept graphs; LLM-assisted dedup
  (exact normalised-name match within the same subject, then LLM duplicates).

## Local environment (this machine)

- Another project's containers own ports 5432 / 6379 / 8000 — **never stop them**. PrepBud's root
  `.env` uses Postgres **5433**, Redis **6380**; run the API on **8001**.
- `docker compose up -d --wait` starts Postgres, Redis, Qdrant (6333) and S3 = **SeaweedFS 4.48**
  (8333; MinIO stopped publishing images; ≤3.97 lacks CORS preflight). The S3 healthcheck creates
  the bucket. Local S3 keys: `infra/seaweedfs/s3.json`.
- Mock OpenAI-compatible LLM for E2E/smoke: `node frontend/tests/e2e/support/mock-llm.mjs` (:54330);
  point `LLM_BASE_URL=http://127.0.0.1:54330/v1`. Real LLM needs the user's `LLM_API_KEY`.
- **LLM = local Ollama** (user's laptop: i5-1145G7, 16 GB RAM, no GPU). Models `qwen2.5:3b` (all chat
  tasks) + `nomic-embed-text` (768-dim, Qdrant collection `document_sections_nomic_768`). Configured in
  the root `.env` (`LLM_PROVIDERS__OLLAMA__*`, `LLM_TASKS__*`, `CONTENT__*`); `OLLAMA_CONTEXT_LENGTH=8192`
  is set as a user env var. Verified end to end: ~90 s for a 1-chunk document; the 3B model misses
  concepts sometimes — expect weak quality and slow 50-page documents (AC-3.6 assumes a hosted model).
  If Ollama is unreachable run `ollama serve`. Switching provider = `.env` only (see `.env.example`).
- Windows: Celery worker needs `--pool=solo`. Tesseract may be missing locally (OCR tests skip;
  CI installs it).
- `make` may be unavailable on Windows — run the recipe commands from `Makefile` directly.

## Commands

```text
backend:  uv sync · uv run alembic upgrade head · uv run uvicorn app.main:app --reload --port 8001
          uv run celery -A app.workers.celery_app worker --pool=solo -l info
          uv run pytest --cov · uv run ruff check . && uv run ruff format --check . · uv run mypy app alembic/env.py
frontend: npm run dev · npm test · npm run lint · npm run typecheck · npm run build · npx playwright test
```

## Gotchas learned

- asyncpg can't run multi-statement strings → split DDL.
- Model FTS index expressions must match PostgreSQL's reflected parenthesisation (migration test
  compares metadata).
- `OwnedViaParentRepository.owner_column` must be read via `type(self)`.
- Next build type-checks tests too — type `vi.fn` mocks.
- Postgres TEXT rejects `\x00` — strip NULs from extracted text.
- Don't edit code containing backslashes via bash heredoc + python string replace (escapes get
  mangled: `\b`→backspace, `\n`→newline, matches silently fail). Use the Write/Edit tools.
- Test JWTs are minted at module import → keep `exp_delta` long (suite runs ~9 min with coverage).
- Celery eager `.apply()` re-raises `Retry` when `task_eager_propagates` is on; tests turn it off.
- redis-py pinned to 6.4 by kombu 5.6 (stable); testcontainers then resolves to 4.13 (old import
  path, handled with try/except in tests/integration/conftest.py).
- Keyword retrieval uses an any-term tsquery (ANDs→ORs unless the query has `-exclusion`); the
  concept list `search` filter stays strict AND.
