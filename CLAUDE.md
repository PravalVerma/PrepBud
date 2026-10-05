# CLAUDE.md — School in a Box (repo: PrepBud)

AI adaptive learning platform. Built **phase by phase** from the docs in `docs/`, which are the
single source of truth. Read this file first, then the doc sections relevant to the task.

## Where we are

| Phase | Status |
| --- | --- |
| 1 Architecture & contracts (`docs/`, `walkthrough.md`) | ✅ committed |
| 2 Foundation & data layer | ✅ committed (`53602a2`) |
| 3 Content pipeline | ✅ committed (`5f35580`) |
| 4 Learning engine core | ✅ committed (`12e46c2`) |
| 5 Interactive sessions | ✅ implemented, all AC-5.x verified (backend 866 / 95% cov, vitest 150, E2E 8/8) — **awaiting user review/commit** |
| 6–8 | not started — **ask the user before starting each new phase** |

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
  app/services/student_model, assessment, tutor, learning_engine                        [Phase 4]
  app/api/sessions.py      REST + SSE + WebSocket; learning_engine/tickets.py (WS tickets) [Phase 5]
  app/integrations/        redis, s3, qdrant
  alembic/versions/        hand-written DDL (one statement per execute — asyncpg)
  tests/unit, tests/integration (testcontainers or TEST_DATABASE_URL/TEST_REDIS_URL), tests/ai
frontend/  Next.js 16 (App Router, React 19, Tailwind v4, TanStack Query 5, Zustand 5)
  src/proxy.ts             (Next 16 replacement for middleware) session refresh + route guard
  src/app/api/backend/[...path]/route.ts   BFF: attaches Bearer token server-side
  src/lib/api.ts           browser API client (unwraps envelopes); src/hooks/, src/types/
  src/app/upload, src/app/concepts(/[id]); src/lib/upload.ts (presigned PUT w/ progress)
  src/app/session(/[id]); components/session/*; lib/session-socket.ts (ticket + reconnect);
          stores/session-store.ts (pure event reducer); hooks/use-session-channel.ts
  tests/ (vitest), tests/e2e (Playwright: mock Supabase Auth :54329, mock LLM :54330,
          API :8002, worker on Celery queue "e2e", web :3100)
docs/      the spec — PRODUCT_REQUIREMENTS, ARCHITECTURE(+_DECISIONS), DOMAIN_MODEL, DATA_MODEL,
           API_CONTRACT, AI_SYSTEM_DESIGN, LEARNING_ENGINE, SECURITY_MODEL, TEST_STRATEGY, PHASES
```

## Design decisions already made (keep consistent)

- **Auth/BFF:** browser never holds Supabase tokens. Server actions sign in; session cookies are
  httpOnly + SameSite=Strict; the Next route `/api/backend/*` forwards with the Bearer token.
  Session WebSockets are the one direct browser→API connection, authorised by a ticket (Phase 5).
- Backend JWT: JWKS (ES256/RS256/EdDSA, alg pinned to key) + optional legacy HS256 secret. The
  user's Supabase project signs **ES256** (no JWT secret needed). User+profile auto-provisioned on
  first authenticated call (race-safe upsert).
- Envelope: `{data, meta:{request_id,timestamp}}`; lists add `meta.pagination`; errors
  `{error:{code,message,details}, meta}`; FastAPI validation errors → 400 `VALIDATION_ERROR`.
- Frontend env vars are runtime server-only (`SUPABASE_URL`, `SUPABASE_ANON_KEY`, `API_URL`,
  `SITE_URL`, optional `PUBLIC_API_URL`) — **not** `NEXT_PUBLIC_*`. The WS origin is computed
  server-side (`env.wsUrl`) and passed to the session page as a prop.
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

### Phase 4 decisions (learning engine — services only; REST/WebSocket + UI are Phase 5)

- `services/learning_engine/`: LangGraph `StateGraph` (`orchestrator.py`) runs **one turn per student
  message** (START routes on `pending_input`; ends wherever the student must respond). No LangGraph
  checkpointer (its Redis saver needs Redis Stack): `session_manager.py` checkpoints the full JSON state
  after each turn to Redis `session:{id}:state` (TTL 2 h) **and** `learning_sessions.metadata.checkpoint`;
  load picks the copy with the higher `turn`. Turns serialised by Redis lock `lock:session:{id}`.
- Transient LLM error → `error` event, state untouched (client resends); permanent/budget → graceful
  wrap-up with template summary (`no_llm`). Per-session token budget from `ai_traces.session_id`.
- BKT (`student_model/mastery_tracker.py`) = LEARNING_ENGINE §4.2 (doc **revised** to match: score =
  soft evidence, learning transition weighted by evidence, monotonic clamp; example table pinned by
  `test_documented_examples`). §7.1 also revised: re-explain only after a poor/wrong answer.
  Specialisation edges are stored as the inverse generalisation (`concept_graph.canonical_edge`). Decay applied **on read** from `last_assessed_at` (never compounds).
  SM-2 at session end updates mastery rows (`next_review_at`); study-plan review items are Phase 6.
- MCQ/true-false graded exactly (no LLM); wrong MCQ → distractor's `misconception` tag, else
  `misconception_detection` task. Free text → `answer_evaluation` task. Misconceptions matched by
  `misconception_key` (snake_case == words); resolved after 3 correct in a row.
- Questions stored and reused (same concept/type, |difficulty − target| ≤ 0.15, not asked this session).
- Frustration guard: 5 consecutive failures (or response-time spike) → encouragement, −0.2 difficulty,
  switch to easiest remaining concept; evidence resets after each activation; 2nd activation ends session.
- Protocol additions documented in API_CONTRACT §3.9 notes (`request_hint`, `session_started`,
  `tutor_message`, `end_reason` values). Fake LLM in `tests/fakes.py` recognises prompts by their
  `## Task: …` headings.

### Phase 5 decisions (interactive sessions)

- `POST /sessions` **plans only** (status `initialising`, no LLM call); opening a channel starts
  teaching (`begin` graph node). Channels: WebSocket `/sessions/{id}/ws?ticket=` (primary, used by the
  UI) and SSE `POST /sessions/{id}/messages` (`Accept: text/event-stream`; JSON without it; works
  through the BFF, which pipes event streams unbuffered). Every turn ends with `turn_complete` = the
  session view (`status`, `awaiting`, `current_question`, …) — clients take state from it.
- WS auth: BFF calls `POST /sessions/{id}/ws-ticket` → 256-bit token in Redis `ws_ticket:{token}`
  (60 s, GETDEL single use, bound to user + session). Handshake checks Origin ∈ `FRONTEND_URL`, ticket,
  `is_active`; failures close 1008. Messages rate-limited 60/min (`ws` scope; pings exempt);
  validation/conflict errors are `error` events, the socket stays open.
- Drop ⇒ session `paused` (`paused_at`; paused time excluded from the time budget via
  `paused_seconds`); reconnect/any message ⇒ unpause and re-send the open question/explanation.
  Explicit `POST /pause|/resume` too. Logged as `session_paused`/`session_resumed` events.
- `objective.target_concepts[].mastery` (starting mastery) added so the UI's mastery bar has a
  baseline (AC-5.4). API_CONTRACT §3.8/3.9 rewritten for all of the above.
- UI: markdown via react-markdown + remark-gfm + remark-math + rehype-katex (`trust:false`, no raw
  HTML); `normaliseMath` rewrites `\(…\)`/`\[…\]` and one-line `$$…$$` to remark-math forms.
  Ended sessions render the stored summary (no socket). Session detail query is read once per visit
  (`gcTime: 0`, no refetch) so a refetch never swaps the live transcript; `session_ended` invalidates
  only the list key.
- E2E mock LLM answers session prompts by `## Task:` heading (MCQ with option **A** correct, evaluation,
  misconception, plain-text summary) and streams tutor text word by word for `stream: true`.

## Local environment (this machine)

- Another project's containers own ports 5432 / 6379 / 8000 — **never stop them**. PrepBud's root
  `.env` uses Postgres **5433**, Redis **6380**; run the API on **8001**.
- `docker compose up -d --wait` starts Postgres, Redis, Qdrant (6333) and S3 = **SeaweedFS 4.48**
  (8333; MinIO stopped publishing images; ≤3.97 lacks CORS preflight). The S3 healthcheck creates
  the bucket. Local S3 keys: `infra/seaweedfs/s3.json`.
- Mock OpenAI-compatible LLM for E2E/smoke: `node frontend/tests/e2e/support/mock-llm.mjs` (:54330);
  point `LLM_BASE_URL=http://127.0.0.1:54330/v1`. Real LLM needs the user's `LLM_API_KEY`.
- **LLM = Google Gemini free tier** via the OpenAI-compatible endpoint
  (`LLM_BASE_URL=https://generativelanguage.googleapis.com/v1beta/openai`, key in `LLM_API_KEY`, provider
  name `gemini`). Heavy tasks (concept extraction, tutor, misconception) → `gemini-3.6-flash`; light
  (questions, grading, summary) → `gemini-3.5-flash-lite`; embeddings `gemini-embedding-001` @ 768 dims in
  Qdrant collection `document_sections_gemini_768`; `AI_PRICING` = 0 (free tier). Verified end to end
  2026-10-05 (doc 36 s, session turns 1–12 s). Gotchas: Google returns 404 for retired models (2.5 is
  retired for new users) and 503 "high demand" for the newest (3.7/3.8) — the provider retries 503s.
  Free-tier prompts may be used by Google: test material only. Ollama (qwen2.5:3b, nomic-embed-text)
  stays installed as an offline fallback (`.env.example` has both blocks). Changing the embedding model
  ⇒ new collection + `processor.reindex()` for ready documents.
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
- Windows console is cp1252: run ad-hoc scripts that print model output with
  `PYTHONIOENCODING=utf-8`. Don't run `uv run …` commands concurrently with a pytest run (uv may
  reinstall the project and the spawn fails with "pytest not found").
- Real small models (qwen2.5:3b) sometimes produce wrong answer keys and use `\( \)` LaTeX (the
  session renderer normalises it).
- WebSocket tests: `httpx-ws` `ASGIWebSocketTransport` + `aconnect_ws`; open the client **inside the
  test** (a fixture tears the anyio task group down in another task → "cancel scope" errors);
  leaving the client waits for the server side (e.g. pause-on-disconnect). A rejected handshake
  arrives as `WebSocketDisconnect(1008)` wrapped in an ExceptionGroup → `pytest.RaisesGroup`.
- Python on this machine defaults to cp1252 for file I/O: ad-hoc patch scripts must use
  `encoding="utf-8"` (or `PYTHONUTF8=1`). Bash heredocs containing quotes/backticks break —
  write scripts with the Write tool.
- Keyword retrieval uses an any-term tsquery (ANDs→ORs unless the query has `-exclusion`); the
  concept list `search` filter stays strict AND.
