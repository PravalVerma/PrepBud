# Development Phases — School in a Box

> **Version:** 0.2.0  
> **Last updated:** 2026-10-05  
> **Status:** Phases 1–7 complete · Phase 8 next

---

## Phase Overview

```
Phase 1: Architecture & Contracts      ✅
Phase 2: Foundation & Data Layer       ✅  53602a2
Phase 3: Content Pipeline              ✅  5f35580
Phase 4: Learning Engine Core          ✅  12e46c2
Phase 5: Interactive Sessions          ✅  1c43adb
Phase 6: Study Plans & Review          ✅  6f7774f
Phase 7: Dashboard & Polish            ✅
Phase 8: Deployment & Launch           ← next
```

Each completed phase lists its deliverables as built, with an **Implementation notes** block
recording where the implementation deliberately differs from the original plan (the other
docs carry the matching "implementation notes" sections). The repo-root `CLAUDE.md` holds the
running log of decisions and environment notes.

---

## Phase 1: Architecture & Contracts ✅

**Goal:** Define the product, architecture, and contracts before writing application code.

**Deliverables:**
- [x] `docs/PRODUCT_REQUIREMENTS.md`
- [x] `docs/ARCHITECTURE.md`
- [x] `docs/ARCHITECTURE_DECISIONS.md`
- [x] `docs/DOMAIN_MODEL.md`
- [x] `docs/AI_SYSTEM_DESIGN.md`
- [x] `docs/SECURITY_MODEL.md`
- [x] `docs/DATA_MODEL.md`
- [x] `docs/LEARNING_ENGINE.md`
- [x] `docs/API_CONTRACT.md`
- [x] `docs/TEST_STRATEGY.md`
- [x] `docs/DEVELOPMENT_PHASES.md`
- [x] Architecture diagram
- [x] Domain model diagram
- [x] Data-flow diagram
- [x] AI workflow diagram
- [x] Unresolved product assumptions
- [x] Proposed repository structure
- [x] Phase 2 acceptance criteria

**Exit Criteria:** All documents reviewed and approved.

---

## Phase 2: Foundation & Data Layer ✅

**Goal:** Set up the project, database schema, authentication, and basic CRUD API.

### Deliverables

#### Backend Foundation
- [x] Python project setup (Poetry / pyproject.toml)
- [x] FastAPI application skeleton
- [x] Configuration system (`pydantic-settings`)
- [x] SQLAlchemy 2.0 models for all entities
- [x] Alembic migration setup + initial migration
- [x] Database connection management (async)
- [x] Base repository pattern (user-scoped queries)

#### Authentication
- [x] Supabase Auth integration
- [x] JWT verification middleware
- [x] User auto-creation on first login
- [x] `get_current_user` dependency

#### Core CRUD Endpoints
- [x] `GET/POST /subjects`
- [x] `GET/POST /courses`
- [x] `GET/POST /chapters`
- [x] `GET/POST /sections`
- [x] `GET/PATCH /profile`
- [x] `GET /health`

#### Frontend Foundation
- [x] Next.js project setup (App Router, TypeScript)
- [x] Authentication flow (Supabase client SDK)
- [x] Basic layout (sidebar, header, content area)
- [x] API client utility
- [x] Basic pages: login, dashboard (skeleton), profile

#### Infrastructure
- [x] Docker Compose for local development (PostgreSQL, Redis)
- [x] Environment variable template (`.env.example`)
- [x] CI pipeline: lint + unit tests

#### Tests
- [x] Unit tests: Pydantic model validation
- [x] Integration tests: CRUD endpoints + user isolation
- [x] Auth tests: JWT verification, user auto-creation

### Acceptance Criteria (Phase 2)

1. **AC-2.1:** A user can sign up via Supabase Auth and a `User` + `StudentProfile` record is created in PostgreSQL on first API call.
2. **AC-2.2:** All CRUD endpoints return correct data, scoped to the authenticated user.
3. **AC-2.3:** Accessing another user's resources returns 404 (not 403).
4. **AC-2.4:** Requests without a valid JWT return 401.
5. **AC-2.5:** The database schema matches `DATA_MODEL.md` — all tables created via Alembic migration.
6. **AC-2.6:** `GET /health` returns service status including database connectivity.
7. **AC-2.7:** Frontend displays login page, authenticates, and renders a dashboard skeleton.
8. **AC-2.8:** Docker Compose starts all local dependencies with a single command.
9. **AC-2.9:** CI pipeline passes: linting, type checking, and all tests green.
10. **AC-2.10:** Test coverage for backend unit + integration tests ≥ 80%.

**Implementation notes:**
- Python project uses **uv** (`pyproject.toml` + `uv.lock`) rather than Poetry.
- Auth: the browser never holds Supabase tokens — Next.js server actions sign in, session
  cookies are httpOnly + SameSite=Strict, and a backend-for-frontend route
  (`/api/backend/*`) forwards API calls with the Bearer token. Backend JWT verification via
  JWKS (ES256/RS256/EdDSA) with optional legacy HS256 secret.
- Curriculum collections are nested (`/subjects/{id}/courses`, `/courses/{id}/chapters`,
  `/chapters/{id}/sections`); items are addressed directly.
- CI: GitHub Actions (`.github/workflows/test.yml`) — lint, type check, tests, E2E.

---

## Phase 3: Content Pipeline ✅

**Goal:** Enable document upload, processing, concept extraction, and search.

### Deliverables

#### Document Upload
- [x] S3 presigned URL generation endpoint
- [x] Upload confirmation endpoint
- [x] Document status tracking

#### Document Processing (Celery)
- [x] Celery + Redis setup
- [x] Text extraction (PDF → text via PyPDF2 / pdfplumber)
- [x] OCR fallback (Tesseract for image-heavy PDFs)
- [x] Semantic chunking
- [x] Concept extraction (LLM)
- [x] Concept relationship detection (LLM)
- [x] Embedding generation
- [x] Qdrant vector storage
- [x] Document ↔ Concept linking

#### LLM Client
- [x] Provider-abstracted LLM client
- [x] OpenAI implementation
- [x] Configuration-driven model selection
- [x] Cost tracking (`AIInteraction` logging)

#### Content Retrieval
- [x] Vector search (Qdrant)
- [x] Keyword search (PostgreSQL full-text)
- [x] Hybrid search with merge and rank

#### Frontend
- [x] Document upload UI (drag & drop, progress bar)
- [x] Document list with processing status
- [x] Concept browser (list + search)
- [x] Concept detail page (description, documents, prerequisites)

#### Tests
- [x] Unit tests: chunking algorithm, concept extraction parsing
- [x] Integration tests: upload flow, processing pipeline (mocked LLM)
- [x] AI tests: concept extraction prompt produces valid JSON structure

### Acceptance Criteria (Phase 3)

1. **AC-3.1:** A student can upload a PDF and see it transition from `pending` → `processing` → `ready`.
2. **AC-3.2:** Extracted concepts appear in the concept list with names and descriptions.
3. **AC-3.3:** Concept prerequisite relationships are detected and stored.
4. **AC-3.4:** Document sections are searchable via both keyword and semantic (vector) search.
5. **AC-3.5:** All LLM calls are logged in `ai_interactions` with cost estimates.
6. **AC-3.6:** Processing a 50-page PDF completes within 5 minutes.
7. **AC-3.7:** The LLM client works with any OpenAI-compatible provider via config change.

**Implementation notes:**
- Text extraction uses **pdfplumber** (not PyPDF2), Tesseract OCR for text-less pages/images.
- One OpenAI-compatible provider class serves OpenAI, Gemini, Groq, OpenRouter and Ollama;
  provider + model are per-task configuration (AC-3.7).
- Local S3 is SeaweedFS (MinIO no longer publishes images).
- `GET /search` (hybrid vector + full-text, reciprocal-rank fusion) was **added** to
  API_CONTRACT §3.6.1 — the contract had no search endpoint.
- AC-3.6 assumes a hosted model; a local 3B model is markedly slower.

---

## Phase 4: Learning Engine Core ✅

**Goal:** Implement the adaptive learning algorithms and session orchestration.

### Deliverables

#### Student Model
- [x] `StudentConceptMastery` CRUD
- [x] Mastery update algorithm (BKT)
- [x] Mastery decay (Ebbinghaus)
- [x] Mastery caching (Redis)

#### Assessment Engine
- [x] Question generation (LLM)
- [x] Answer evaluation (LLM)
- [x] Misconception detection (LLM)
- [x] Difficulty calibration
- [x] Question type selection by mastery level

#### Concept Selection
- [x] Prerequisite graph traversal
- [x] Concept prioritisation algorithm
- [x] Prerequisite satisfaction check

#### Session Orchestration (LangGraph)
- [x] Session state definition
- [x] LangGraph workflow graph
- [x] Node implementations (INIT, PLAN, EXPLAIN, PRACTICE, REVIEW, EVALUATE, UPDATE, DECIDE, WRAP, SCHEDULE)
- [x] State persistence (Redis checkpointing)
- [x] Session resumption

#### Tutor
- [x] Prompt templates (explain, re-explain, worked example, Socratic, follow-up)
- [x] Context assembly (student profile + content + mastery + history)
- [x] Streaming response support

#### Tests
- [x] Unit tests: mastery update, SM-2 scheduling, difficulty calibration, concept selection
- [x] Integration tests: full session flow (mocked LLM), mastery persistence
- [x] AI tests: question generation and evaluation output structure

### Acceptance Criteria (Phase 4)

1. **AC-4.1:** Correct answers increase mastery; incorrect answers decrease it.
2. **AC-4.2:** Mastery stays within [0.0, 1.0].
3. **AC-4.3:** Question difficulty adapts to student mastery level.
4. **AC-4.4:** The session state machine transitions correctly through all states.
5. **AC-4.5:** Session state is recoverable after interruption (Redis persistence).
6. **AC-4.6:** Misconceptions are detected and stored when AI identifies them.
7. **AC-4.7:** Generated questions have valid structure and reference the target concept.
8. **AC-4.8:** Frustration guard activates after 5 consecutive failures.

**Implementation notes:**
- One LangGraph turn per student message; state is checkpointed as JSON to Redis **and**
  PostgreSQL (`learning_sessions.metadata.checkpoint`) instead of a LangGraph checkpointer
  (whose Redis saver needs Redis Stack).
- LEARNING_ENGINE §4.2 (BKT) and §7.1 were revised to match the implementation (soft evidence,
  monotonic updates; re-explain only after a poor answer).

---

## Phase 5: Interactive Sessions ✅

**Goal:** Build the real-time session UI and WebSocket communication.

### Deliverables

#### Backend
- [x] WebSocket endpoint for session interaction
- [x] SSE streaming for tutor explanations
- [x] Session message protocol (client ↔ server)
- [x] Session lifecycle management (start, pause, resume, end)

#### Frontend
- [x] Session page with chat-like interface
- [x] Streamed explanation rendering (markdown + LaTeX)
- [x] Question display (MCQ, short answer, true/false, etc.)
- [x] Answer input and submission
- [x] Evaluation display with feedback
- [x] Mastery update animation
- [x] Hint request button
- [x] Follow-up question input
- [x] Session summary at end
- [x] Session history list

#### Tests
- [x] Integration tests: WebSocket message flow
- [x] E2E test: complete session journey (sign in → session → summary)

### Acceptance Criteria (Phase 5)

1. **AC-5.1:** A student can start a session and see a streamed explanation.
2. **AC-5.2:** A student can answer questions and receive immediate evaluation.
3. **AC-5.3:** The session adapts (explains after failure, increases difficulty after success).
4. **AC-5.4:** Mastery updates are visible in real time during the session.
5. **AC-5.5:** A session can be ended by the student and shows a summary.
6. **AC-5.6:** LaTeX math renders correctly in explanations and questions.
7. **AC-5.7:** The session gracefully handles LLM errors (retry, fallback message).

**Implementation notes:**
- `POST /sessions` only plans; teaching starts when a channel opens. The WebSocket is
  authorised by a single-use 60 s ticket from `POST /sessions/{id}/ws-ticket` (the browser
  holds no JWT); the same turns are available as SSE via `POST /sessions/{id}/messages`.
- Every turn ends with a `turn_complete` event carrying the session view; a dropped socket
  pauses the session. API_CONTRACT §3.8–3.9 were rewritten accordingly.

---

## Phase 6: Study Plans & Review ✅

**Goal:** Implement spaced repetition scheduling and study plan management.

### Deliverables

#### Backend
- [x] SM-2 scheduling implementation
- [x] Study plan generation algorithm
- [x] Study plan regeneration on session completion
- [x] Review session type (focused on due items)
- [x] Mastery decay Celery Beat job
- [x] Study plan API endpoints

#### Frontend
- [x] Study plan page (calendar or list view)
- [x] Review queue ("X items due today")
- [x] Quick review session start
- [x] Goal creation and management
- [x] Goal progress tracking

#### Tests
- [x] Unit tests: SM-2 algorithm, study plan generation, decay
- [x] Integration tests: plan generation on session end, review item status updates
- [x] E2E test: review flow journey

### Acceptance Criteria (Phase 6)

1. **AC-6.1:** After a session, next review dates are computed for practiced concepts.
2. **AC-6.2:** Overdue review items appear at the top of the study plan.
3. **AC-6.3:** Creating a learning goal generates a study plan automatically.
4. **AC-6.4:** Review intervals increase with consecutive successful reviews.
5. **AC-6.5:** Mastery decays over time for unreviewed concepts.
6. **AC-6.6:** A student can start a review session focused on due items.

**Implementation notes:**
- One active plan per user covers all active goals; regenerating supersedes it and carries
  today's completed/skipped items over. Plan recalculation runs inline (cheap, and the UI must
  reflect it immediately); Celery Beat runs the daily decay + plan-refresh job.
- Decay is computed on read and also materialised daily into `mastery_level` (exponential
  decay composes, so it never compounds).
- Concepts per day round up so deadlines are met. See LEARNING_ENGINE §5.2/§8 notes and
  API_CONTRACT §3.7/§3.10.

---

## Phase 7: Dashboard & Polish ✅

**Goal:** Build the mastery dashboard, polish the UI, optimise performance.

### Deliverables

#### Frontend
- [x] Mastery dashboard (overall progress, per-subject, per-concept)
- [x] Mastery heatmap or progress chart
- [x] Concept graph visualisation (interactive)
- [x] Misconception list with status
- [x] Activity history (sessions, study time)
- [x] Onboarding flow (guided first-use experience)
- [x] Responsive design (tablet-friendly)
- [x] Dark mode
- [x] Loading states and error boundaries
- [x] Toast notifications

#### Backend
- [x] Dashboard aggregation endpoints
- [x] Performance optimisation (query tuning, caching)
- [x] AI cost tracking endpoint
- [x] Error handling polish (structured errors, retries)

#### Tests
- [x] E2E tests: all critical journeys
- [x] Performance benchmarks
- [x] Accessibility audit (basic)

### Acceptance Criteria (Phase 7)

1. **AC-7.1:** Dashboard shows accurate mastery data across subjects.
2. **AC-7.2:** Concept graph is interactive and shows mastery colour-coding.
3. **AC-7.3:** All pages load within 2 seconds.
4. **AC-7.4:** The onboarding flow guides a new user from sign-up to first session.
5. **AC-7.5:** The UI is usable on tablet-sized screens.
6. **AC-7.6:** All critical journeys pass E2E tests.

**Implementation notes:**
- New endpoints: `GET /mastery/overview` (per-subject + overall stats, mastery distribution,
  daily activity in the student's timezone, streak, misconception counts), `GET /mastery/heatmap`
  (concepts × weeks from mastery history), `GET /misconceptions`, `GET /ai/usage`
  (API_CONTRACT §3.11–3.13, extended with the fields the UI needs). Aggregates are cached per user
  in Redis for 60 s and invalidated by session completion, document processing and the daily job.
- Dashboard: onboarding checklist (derived from real data; advances `onboarding_state`), mastery
  overview, review queue, activity chart, recent sessions, open misconceptions. A new **Progress**
  page adds the heatmap, misconception list with evidence, AI usage vs. budget and session history.
  The interactive concept map (layered prerequisite layout, mastery colours, depth 1–3, keyboard
  accessible) is on each concept page.
- Dark mode remaps Tailwind's colour variables under `.dark` instead of adding `dark:` classes to
  every component; a pre-paint script avoids a flash. Theme choice: system / light / dark.
- Toasts are driven by React Query mutation `meta` (`success`, `errorToast`); queries retry
  network/5xx/429 with backoff, never other 4xx. Every section has `loading.tsx` / `error.tsx`.
- Tablet: the sidebar is a drawer below the `lg` breakpoint.
- Performance: the DB pool is warmed at startup (asyncpg's SCRAM login runs on the event loop);
  a backend benchmark checks the dashboard endpoints with 300 concepts / 3 000 answers, and an E2E
  test asserts every page renders within 2 s (AC-7.3).
- Accessibility: an axe audit (WCAG 2 A/AA, serious + critical) runs in E2E on every page in light
  mode and on the main pages in dark mode.

---

## Phase 8: Deployment & Launch

**Goal:** Deploy to production, monitoring, and initial user testing.

### Deliverables

- [ ] Production deployment (backend: Railway/Fly.io, frontend: Vercel)
- [ ] Managed database provisioning (Supabase / Neon)
- [ ] Managed Redis (Upstash)
- [ ] Qdrant Cloud setup
- [ ] S3 / R2 bucket setup
- [ ] Environment configuration
- [ ] CI/CD pipeline (deploy on merge to main)
- [ ] Health monitoring
- [ ] Error tracking (Sentry)
- [ ] AI cost monitoring dashboard
- [ ] Backup strategy
- [ ] Production seed data (demo account)
- [ ] User acceptance testing (2–3 beta testers)
- [ ] Bug fixes from beta feedback

### Acceptance Criteria (Phase 8)

1. **AC-8.1:** Application is accessible via public URL.
2. **AC-8.2:** A new user can complete the full onboarding → session → review cycle.
3. **AC-8.3:** Errors are captured and reported (Sentry or equivalent).
4. **AC-8.4:** Daily AI cost is tracked and stays under budget.
5. **AC-8.5:** Database backups run daily.
6. **AC-8.6:** Beta testers report no data-loss bugs.

---

## Unresolved Product Assumptions

These require decisions before or during implementation:

| # | Assumption | Impact | Proposed Default | Needs Decision By |
|---|---|---|---|---|
| 1 | **Concept granularity**: How fine-grained? | Affects extraction prompts, mastery tracking | "Assessable and teachable in 2–5 minutes" | Phase 3 |
| 2 | **Mastery threshold for "learned"**: 0.80? | Affects when concepts move to review | 0.80 | Phase 4 |
| 3 | **Session duration**: Fixed or flexible? | Affects UX and fatigue management | Default 30 min, student-adjustable | Phase 5 |
| 4 | **Content licensing**: Track copyright? | Legal risk for uploaded material | No (student's own material; terms of service cover this) | Phase 3 |
| 5 | **LLM failure fallback**: Graceful degradation? | Affects reliability | End session with message; no degraded mode | Phase 4 |
| 6 | **Pre-built concept graphs**: For common subjects? | Affects cold-start experience | No; derive everything from uploaded material | Phase 3 |
| 7 | **Multi-document concept merging**: How to handle? | Affects concept graph quality | LLM-assisted deduplication during extraction | Phase 3 |
| 8 | **Cost per session target**: $0.10 average? | Affects model selection | $0.10 target; monitor and adjust | Phase 4 |
| 9 | **Question persistence**: Reuse generated questions? | Affects quality and cost | Store and reuse; regenerate on demand | Phase 4 |
| 10 | **Mastery decay rate**: How aggressive? | Affects review scheduling pressure | Conservative (ease_factor * 10 stability) | Phase 6 |

---

## Proposed Repository Structure

```
school-in-a-box/
├── README.md
├── docs/
│   ├── PRODUCT_REQUIREMENTS.md
│   ├── ARCHITECTURE.md
│   ├── ARCHITECTURE_DECISIONS.md
│   ├── DOMAIN_MODEL.md
│   ├── AI_SYSTEM_DESIGN.md
│   ├── SECURITY_MODEL.md
│   ├── DATA_MODEL.md
│   ├── LEARNING_ENGINE.md
│   ├── API_CONTRACT.md
│   ├── TEST_STRATEGY.md
│   └── DEVELOPMENT_PHASES.md
│
├── backend/
│   ├── pyproject.toml
│   ├── alembic.ini
│   ├── alembic/
│   │   └── versions/
│   ├── app/
│   │   ├── __init__.py
│   │   ├── main.py                    # FastAPI app factory
│   │   ├── config.py                  # Pydantic settings
│   │   │
│   │   ├── api/                       # Route handlers
│   │   │   ├── __init__.py
│   │   │   ├── deps.py                # Shared dependencies
│   │   │   ├── auth.py
│   │   │   ├── profile.py
│   │   │   ├── subjects.py
│   │   │   ├── documents.py
│   │   │   ├── concepts.py
│   │   │   ├── sessions.py
│   │   │   ├── goals.py
│   │   │   ├── mastery.py
│   │   │   ├── study_plan.py
│   │   │   ├── misconceptions.py
│   │   │   └── health.py
│   │   │
│   │   ├── core/                      # Cross-cutting
│   │   │   ├── __init__.py
│   │   │   ├── security.py            # JWT verification
│   │   │   ├── exceptions.py          # Custom exceptions
│   │   │   ├── middleware.py          # CORS, security headers, rate limiting
│   │   │   └── logging.py            # Structured logging
│   │   │
│   │   ├── domain/                    # Domain models (Pydantic schemas)
│   │   │   ├── __init__.py
│   │   │   ├── user.py
│   │   │   ├── curriculum.py          # Subject, Course, Chapter, Section
│   │   │   ├── content.py            # Document, DocumentSection
│   │   │   ├── concept.py
│   │   │   ├── session.py
│   │   │   ├── assessment.py         # Question, QuestionAttempt
│   │   │   ├── mastery.py
│   │   │   ├── study_plan.py
│   │   │   └── ai.py                 # AIInteraction, AITrace
│   │   │
│   │   ├── db/                        # Database layer
│   │   │   ├── __init__.py
│   │   │   ├── base.py               # SQLAlchemy base, engine, session
│   │   │   ├── models/               # SQLAlchemy ORM models
│   │   │   │   ├── __init__.py
│   │   │   │   ├── user.py
│   │   │   │   ├── curriculum.py
│   │   │   │   ├── content.py
│   │   │   │   ├── concept.py
│   │   │   │   ├── session.py
│   │   │   │   ├── assessment.py
│   │   │   │   ├── mastery.py
│   │   │   │   ├── study_plan.py
│   │   │   │   └── ai.py
│   │   │   └── repositories/         # Data access layer
│   │   │       ├── __init__.py
│   │   │       ├── base.py           # BaseRepository (user-scoped)
│   │   │       ├── user.py
│   │   │       ├── concept.py
│   │   │       ├── document.py
│   │   │       ├── session.py
│   │   │       ├── mastery.py
│   │   │       └── study_plan.py
│   │   │
│   │   ├── services/                  # Business logic
│   │   │   ├── __init__.py
│   │   │   ├── learning_engine/
│   │   │   │   ├── __init__.py
│   │   │   │   ├── orchestrator.py   # LangGraph workflow
│   │   │   │   ├── concept_selector.py
│   │   │   │   ├── session_manager.py
│   │   │   │   └── state.py          # Session state definition
│   │   │   ├── student_model/
│   │   │   │   ├── __init__.py
│   │   │   │   ├── mastery_tracker.py
│   │   │   │   ├── misconception_tracker.py
│   │   │   │   └── review_scheduler.py
│   │   │   ├── assessment/
│   │   │   │   ├── __init__.py
│   │   │   │   ├── question_generator.py
│   │   │   │   ├── answer_evaluator.py
│   │   │   │   └── difficulty_calibrator.py
│   │   │   ├── content/
│   │   │   │   ├── __init__.py
│   │   │   │   ├── document_processor.py
│   │   │   │   ├── chunker.py
│   │   │   │   ├── concept_extractor.py
│   │   │   │   └── retriever.py
│   │   │   ├── tutor/
│   │   │   │   ├── __init__.py
│   │   │   │   ├── tutor.py
│   │   │   │   └── context_builder.py
│   │   │   └── study_plan/
│   │   │       ├── __init__.py
│   │   │       └── plan_generator.py
│   │   │
│   │   ├── ai/                        # AI infrastructure
│   │   │   ├── __init__.py
│   │   │   ├── llm_client.py         # Provider abstraction
│   │   │   ├── providers/
│   │   │   │   ├── __init__.py
│   │   │   │   ├── openai.py
│   │   │   │   └── base.py
│   │   │   ├── prompts/              # Prompt templates (.md files)
│   │   │   │   ├── tutor/
│   │   │   │   ├── assessment/
│   │   │   │   ├── content/
│   │   │   │   └── session/
│   │   │   ├── cost_tracker.py
│   │   │   └── prompt_manager.py     # Template loading + rendering
│   │   │
│   │   ├── workers/                   # Celery tasks
│   │   │   ├── __init__.py
│   │   │   ├── celery_app.py
│   │   │   ├── document_tasks.py
│   │   │   ├── embedding_tasks.py
│   │   │   ├── study_plan_tasks.py
│   │   │   └── maintenance_tasks.py  # Decay, cleanup
│   │   │
│   │   └── integrations/             # External service clients
│   │       ├── __init__.py
│   │       ├── qdrant.py
│   │       ├── s3.py
│   │       └── redis.py
│   │
│   └── tests/
│       ├── conftest.py
│       ├── unit/
│       │   ├── test_mastery_update.py
│       │   ├── test_sm2_scheduler.py
│       │   ├── test_concept_selector.py
│       │   ├── test_difficulty_calibrator.py
│       │   ├── test_chunker.py
│       │   └── test_study_plan_generator.py
│       ├── integration/
│       │   ├── test_auth.py
│       │   ├── test_subjects_api.py
│       │   ├── test_documents_api.py
│       │   ├── test_concepts_api.py
│       │   ├── test_sessions_api.py
│       │   └── test_user_isolation.py
│       └── ai/
│           ├── test_prompt_assembly.py
│           ├── test_question_generation.py
│           └── test_answer_evaluation.py
│
├── frontend/
│   ├── package.json
│   ├── tsconfig.json
│   ├── next.config.js
│   ├── public/
│   ├── src/
│   │   ├── app/                      # Next.js App Router
│   │   │   ├── layout.tsx
│   │   │   ├── page.tsx              # Landing / redirect to dashboard
│   │   │   ├── login/
│   │   │   ├── dashboard/
│   │   │   ├── upload/
│   │   │   ├── concepts/
│   │   │   │   └── [id]/
│   │   │   ├── session/
│   │   │   │   └── [id]/
│   │   │   ├── goals/
│   │   │   ├── review/
│   │   │   └── profile/
│   │   ├── components/
│   │   │   ├── ui/                   # Reusable UI primitives
│   │   │   ├── session/              # Session-specific components
│   │   │   ├── dashboard/            # Dashboard widgets
│   │   │   ├── concepts/             # Concept browser components
│   │   │   └── layout/              # Shell, sidebar, header
│   │   ├── lib/
│   │   │   ├── api.ts               # API client
│   │   │   ├── auth.ts              # Supabase auth helpers
│   │   │   ├── supabase.ts          # Supabase client init
│   │   │   └── utils.ts
│   │   ├── hooks/
│   │   │   ├── use-session.ts
│   │   │   ├── use-mastery.ts
│   │   │   └── use-auth.ts
│   │   ├── stores/
│   │   │   └── session-store.ts     # Zustand store for active session
│   │   └── types/
│   │       ├── api.ts               # API response types
│   │       ├── domain.ts            # Domain entity types
│   │       └── session.ts           # WebSocket message types
│   └── tests/
│       ├── components/
│       └── e2e/
│           └── session.spec.ts
│
├── docker-compose.yml                # Local dev: PostgreSQL, Redis
├── docker-compose.test.yml           # Test: PostgreSQL, Redis for CI
├── .env.example
├── .gitignore
├── .github/
│   └── workflows/
│       ├── test.yml
│       └── deploy.yml
└── Makefile                          # Common commands
```

---

## Timeline Estimates

| Phase | Estimated Duration | Dependencies |
|---|---|---|
| Phase 1: Architecture | ✅ Complete | — |
| Phase 2: Foundation | ✅ Complete | Phase 1 |
| Phase 3: Content Pipeline | ✅ Complete | Phase 2 |
| Phase 4: Learning Engine | ✅ Complete | Phase 3 |
| Phase 5: Interactive Sessions | ✅ Complete | Phase 4 |
| Phase 6: Study Plans & Review | ✅ Complete | Phase 4 |
| Phase 7: Dashboard & Polish | ✅ Complete | Phase 5, 6 |
| Phase 8: Deployment | 1 week | Phase 7 |
| **Total** | **8–13 weeks** | |

> **Note:** Phases 5 and 6 can partially overlap. Phase 7 can begin as soon as core session functionality works.
