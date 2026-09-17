# Development Phases — School in a Box

> **Version:** 0.1.0-draft  
> **Last updated:** 2026-09-17  
> **Status:** Pre-implementation / Architecture Phase

---

## Phase Overview

```
Phase 1: Architecture & Contracts      ← YOU ARE HERE
Phase 2: Foundation & Data Layer
Phase 3: Content Pipeline
Phase 4: Learning Engine Core
Phase 5: Interactive Sessions
Phase 6: Study Plans & Review
Phase 7: Dashboard & Polish
Phase 8: Deployment & Launch
```

---

## Phase 1: Architecture & Contracts ✅ (Current)

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

## Phase 2: Foundation & Data Layer

**Goal:** Set up the project, database schema, authentication, and basic CRUD API.

### Deliverables

#### Backend Foundation
- [ ] Python project setup (Poetry / pyproject.toml)
- [ ] FastAPI application skeleton
- [ ] Configuration system (`pydantic-settings`)
- [ ] SQLAlchemy 2.0 models for all entities
- [ ] Alembic migration setup + initial migration
- [ ] Database connection management (async)
- [ ] Base repository pattern (user-scoped queries)

#### Authentication
- [ ] Supabase Auth integration
- [ ] JWT verification middleware
- [ ] User auto-creation on first login
- [ ] `get_current_user` dependency

#### Core CRUD Endpoints
- [ ] `GET/POST /subjects`
- [ ] `GET/POST /courses`
- [ ] `GET/POST /chapters`
- [ ] `GET/POST /sections`
- [ ] `GET/PATCH /profile`
- [ ] `GET /health`

#### Frontend Foundation
- [ ] Next.js project setup (App Router, TypeScript)
- [ ] Authentication flow (Supabase client SDK)
- [ ] Basic layout (sidebar, header, content area)
- [ ] API client utility
- [ ] Basic pages: login, dashboard (skeleton), profile

#### Infrastructure
- [ ] Docker Compose for local development (PostgreSQL, Redis)
- [ ] Environment variable template (`.env.example`)
- [ ] CI pipeline: lint + unit tests

#### Tests
- [ ] Unit tests: Pydantic model validation
- [ ] Integration tests: CRUD endpoints + user isolation
- [ ] Auth tests: JWT verification, user auto-creation

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

---

## Phase 3: Content Pipeline

**Goal:** Enable document upload, processing, concept extraction, and search.

### Deliverables

#### Document Upload
- [ ] S3 presigned URL generation endpoint
- [ ] Upload confirmation endpoint
- [ ] Document status tracking

#### Document Processing (Celery)
- [ ] Celery + Redis setup
- [ ] Text extraction (PDF → text via PyPDF2 / pdfplumber)
- [ ] OCR fallback (Tesseract for image-heavy PDFs)
- [ ] Semantic chunking
- [ ] Concept extraction (LLM)
- [ ] Concept relationship detection (LLM)
- [ ] Embedding generation
- [ ] Qdrant vector storage
- [ ] Document ↔ Concept linking

#### LLM Client
- [ ] Provider-abstracted LLM client
- [ ] OpenAI implementation
- [ ] Configuration-driven model selection
- [ ] Cost tracking (`AIInteraction` logging)

#### Content Retrieval
- [ ] Vector search (Qdrant)
- [ ] Keyword search (PostgreSQL full-text)
- [ ] Hybrid search with merge and rank

#### Frontend
- [ ] Document upload UI (drag & drop, progress bar)
- [ ] Document list with processing status
- [ ] Concept browser (list + search)
- [ ] Concept detail page (description, documents, prerequisites)

#### Tests
- [ ] Unit tests: chunking algorithm, concept extraction parsing
- [ ] Integration tests: upload flow, processing pipeline (mocked LLM)
- [ ] AI tests: concept extraction prompt produces valid JSON structure

### Acceptance Criteria (Phase 3)

1. **AC-3.1:** A student can upload a PDF and see it transition from `pending` → `processing` → `ready`.
2. **AC-3.2:** Extracted concepts appear in the concept list with names and descriptions.
3. **AC-3.3:** Concept prerequisite relationships are detected and stored.
4. **AC-3.4:** Document sections are searchable via both keyword and semantic (vector) search.
5. **AC-3.5:** All LLM calls are logged in `ai_interactions` with cost estimates.
6. **AC-3.6:** Processing a 50-page PDF completes within 5 minutes.
7. **AC-3.7:** The LLM client works with any OpenAI-compatible provider via config change.

---

## Phase 4: Learning Engine Core

**Goal:** Implement the adaptive learning algorithms and session orchestration.

### Deliverables

#### Student Model
- [ ] `StudentConceptMastery` CRUD
- [ ] Mastery update algorithm (BKT)
- [ ] Mastery decay (Ebbinghaus)
- [ ] Mastery caching (Redis)

#### Assessment Engine
- [ ] Question generation (LLM)
- [ ] Answer evaluation (LLM)
- [ ] Misconception detection (LLM)
- [ ] Difficulty calibration
- [ ] Question type selection by mastery level

#### Concept Selection
- [ ] Prerequisite graph traversal
- [ ] Concept prioritisation algorithm
- [ ] Prerequisite satisfaction check

#### Session Orchestration (LangGraph)
- [ ] Session state definition
- [ ] LangGraph workflow graph
- [ ] Node implementations (INIT, PLAN, EXPLAIN, PRACTICE, REVIEW, EVALUATE, UPDATE, DECIDE, WRAP, SCHEDULE)
- [ ] State persistence (Redis checkpointing)
- [ ] Session resumption

#### Tutor
- [ ] Prompt templates (explain, re-explain, worked example, Socratic, follow-up)
- [ ] Context assembly (student profile + content + mastery + history)
- [ ] Streaming response support

#### Tests
- [ ] Unit tests: mastery update, SM-2 scheduling, difficulty calibration, concept selection
- [ ] Integration tests: full session flow (mocked LLM), mastery persistence
- [ ] AI tests: question generation and evaluation output structure

### Acceptance Criteria (Phase 4)

1. **AC-4.1:** Correct answers increase mastery; incorrect answers decrease it.
2. **AC-4.2:** Mastery stays within [0.0, 1.0].
3. **AC-4.3:** Question difficulty adapts to student mastery level.
4. **AC-4.4:** The session state machine transitions correctly through all states.
5. **AC-4.5:** Session state is recoverable after interruption (Redis persistence).
6. **AC-4.6:** Misconceptions are detected and stored when AI identifies them.
7. **AC-4.7:** Generated questions have valid structure and reference the target concept.
8. **AC-4.8:** Frustration guard activates after 5 consecutive failures.

---

## Phase 5: Interactive Sessions

**Goal:** Build the real-time session UI and WebSocket communication.

### Deliverables

#### Backend
- [ ] WebSocket endpoint for session interaction
- [ ] SSE streaming for tutor explanations
- [ ] Session message protocol (client ↔ server)
- [ ] Session lifecycle management (start, pause, resume, end)

#### Frontend
- [ ] Session page with chat-like interface
- [ ] Streamed explanation rendering (markdown + LaTeX)
- [ ] Question display (MCQ, short answer, true/false, etc.)
- [ ] Answer input and submission
- [ ] Evaluation display with feedback
- [ ] Mastery update animation
- [ ] Hint request button
- [ ] Follow-up question input
- [ ] Session summary at end
- [ ] Session history list

#### Tests
- [ ] Integration tests: WebSocket message flow
- [ ] E2E test: complete session journey (sign in → session → summary)

### Acceptance Criteria (Phase 5)

1. **AC-5.1:** A student can start a session and see a streamed explanation.
2. **AC-5.2:** A student can answer questions and receive immediate evaluation.
3. **AC-5.3:** The session adapts (explains after failure, increases difficulty after success).
4. **AC-5.4:** Mastery updates are visible in real time during the session.
5. **AC-5.5:** A session can be ended by the student and shows a summary.
6. **AC-5.6:** LaTeX math renders correctly in explanations and questions.
7. **AC-5.7:** The session gracefully handles LLM errors (retry, fallback message).

---

## Phase 6: Study Plans & Review

**Goal:** Implement spaced repetition scheduling and study plan management.

### Deliverables

#### Backend
- [ ] SM-2 scheduling implementation
- [ ] Study plan generation algorithm
- [ ] Study plan regeneration on session completion
- [ ] Review session type (focused on due items)
- [ ] Mastery decay Celery Beat job
- [ ] Study plan API endpoints

#### Frontend
- [ ] Study plan page (calendar or list view)
- [ ] Review queue ("X items due today")
- [ ] Quick review session start
- [ ] Goal creation and management
- [ ] Goal progress tracking

#### Tests
- [ ] Unit tests: SM-2 algorithm, study plan generation, decay
- [ ] Integration tests: plan generation on session end, review item status updates
- [ ] E2E test: review flow journey

### Acceptance Criteria (Phase 6)

1. **AC-6.1:** After a session, next review dates are computed for practiced concepts.
2. **AC-6.2:** Overdue review items appear at the top of the study plan.
3. **AC-6.3:** Creating a learning goal generates a study plan automatically.
4. **AC-6.4:** Review intervals increase with consecutive successful reviews.
5. **AC-6.5:** Mastery decays over time for unreviewe concepts.
6. **AC-6.6:** A student can start a review session focused on due items.

---

## Phase 7: Dashboard & Polish

**Goal:** Build the mastery dashboard, polish the UI, optimise performance.

### Deliverables

#### Frontend
- [ ] Mastery dashboard (overall progress, per-subject, per-concept)
- [ ] Mastery heatmap or progress chart
- [ ] Concept graph visualisation (interactive)
- [ ] Misconception list with status
- [ ] Activity history (sessions, study time)
- [ ] Onboarding flow (guided first-use experience)
- [ ] Responsive design (tablet-friendly)
- [ ] Dark mode
- [ ] Loading states and error boundaries
- [ ] Toast notifications

#### Backend
- [ ] Dashboard aggregation endpoints
- [ ] Performance optimisation (query tuning, caching)
- [ ] AI cost tracking endpoint
- [ ] Error handling polish (structured errors, retries)

#### Tests
- [ ] E2E tests: all critical journeys
- [ ] Performance benchmarks
- [ ] Accessibility audit (basic)

### Acceptance Criteria (Phase 7)

1. **AC-7.1:** Dashboard shows accurate mastery data across subjects.
2. **AC-7.2:** Concept graph is interactive and shows mastery colour-coding.
3. **AC-7.3:** All pages load within 2 seconds.
4. **AC-7.4:** The onboarding flow guides a new user from sign-up to first session.
5. **AC-7.5:** The UI is usable on tablet-sized screens.
6. **AC-7.6:** All critical journeys pass E2E tests.

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
| Phase 2: Foundation | 1–2 weeks | Phase 1 |
| Phase 3: Content Pipeline | 1–2 weeks | Phase 2 |
| Phase 4: Learning Engine | 2–3 weeks | Phase 3 |
| Phase 5: Interactive Sessions | 1–2 weeks | Phase 4 |
| Phase 6: Study Plans & Review | 1 week | Phase 4 |
| Phase 7: Dashboard & Polish | 1–2 weeks | Phase 5, 6 |
| Phase 8: Deployment | 1 week | Phase 7 |
| **Total** | **8–13 weeks** | |

> **Note:** Phases 5 and 6 can partially overlap. Phase 7 can begin as soon as core session functionality works.
