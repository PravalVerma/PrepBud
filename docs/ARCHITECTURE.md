# Architecture — School in a Box

> **Version:** 0.1.0-draft  
> **Last updated:** 2026-09-17  
> **Status:** Pre-implementation / Architecture Phase

---

## 1. Architecture Overview

School in a Box follows a **layered, service-oriented monolith** architecture — a modular monolith deployed as a small number of services, not a full microservices topology. This balances development velocity with clean separation of concerns.

```
┌───────────────────────────────────────────────────────────────────────┐
│                          CLIENT LAYER                                │
│                                                                      │
│   Next.js (React + TypeScript)                                       │
│   ├── Pages / App Router                                             │
│   ├── UI Components (Session, Dashboard, Upload, StudyPlan)          │
│   ├── State Management (React Query / Zustand)                       │
│   └── Real-time: WebSocket / SSE for streaming tutor responses       │
└──────────────────────────┬────────────────────────────────────────────┘
                           │ HTTPS / WSS
┌──────────────────────────▼────────────────────────────────────────────┐
│                          API LAYER                                    │
│                                                                      │
│   FastAPI (Python)                                                    │
│   ├── REST endpoints (CRUD, upload, progress)                        │
│   ├── WebSocket endpoints (learning session streaming)               │
│   ├── Auth middleware (Supabase JWT verification)                     │
│   ├── Rate limiting                                                  │
│   └── Request validation (Pydantic)                                  │
└───┬───────────┬───────────┬───────────┬───────────┬──────────────────┘
    │           │           │           │           │
┌───▼────┐ ┌───▼────┐ ┌───▼────┐ ┌───▼─────┐ ┌───▼──────┐
│Learning│ │Content │ │Student │ │Assessment│ │Background│
│Engine  │ │System  │ │Model   │ │Engine    │ │Jobs      │
└───┬────┘ └───┬────┘ └───┬────┘ └───┬─────┘ └───┬──────┘
    │          │          │           │            │
┌───▼──────────▼──────────▼───────────▼────────────▼───────────────────┐
│                       DATA & INFRASTRUCTURE LAYER                    │
│                                                                      │
│   PostgreSQL ─── Primary data store (entities, mastery, sessions)    │
│   Qdrant ─────── Vector store (document embeddings, concept search)  │
│   Redis ─────── Cache (session state, mastery cache, task broker)    │
│   S3 ────────── Object storage (uploaded documents, processed files) │
└──────────────────────────────────────────────────────────────────────┘
```

---

## 2. System Components

### 2.1 Client Layer — Next.js Frontend

**Responsibility:** Render the student-facing UI, manage client-side state, communicate with the API.

| Concern | Approach |
|---|---|
| Routing | Next.js App Router |
| State | React Query (server state) + Zustand (client state) |
| Streaming | Server-Sent Events (SSE) for tutor explanations; WebSocket for session interactions |
| Auth | Supabase Auth client SDK; JWT stored in httpOnly cookie |
| Styling | CSS Modules or Tailwind (TBD based on team preference) |
| File upload | Direct-to-S3 presigned URL upload, then notify backend |

**Key pages:**
- `/dashboard` — mastery overview, study plan, recent sessions
- `/upload` — document upload and processing status
- `/session/[id]` — active learning session (chat-like interface)
- `/concepts` — concept browser and mastery map
- `/goals` — learning goal management
- `/review` — spaced-repetition review queue

### 2.2 API Layer — FastAPI Backend

**Responsibility:** Business logic, AI orchestration, data access, authentication enforcement.

**Internal modules (not separate services):**

```
backend/
├── api/                 # Route handlers
│   ├── auth.py
│   ├── documents.py
│   ├── sessions.py
│   ├── concepts.py
│   ├── goals.py
│   ├── mastery.py
│   ├── study_plan.py
│   └── health.py
├── core/                # Cross-cutting concerns
│   ├── config.py
│   ├── security.py
│   ├── exceptions.py
│   └── dependencies.py
├── domain/              # Domain models (Pydantic)
│   ├── user.py
│   ├── content.py
│   ├── session.py
│   ├── mastery.py
│   └── assessment.py
├── services/            # Business logic
│   ├── learning_engine/
│   ├── content_service/
│   ├── student_model/
│   ├── assessment_engine/
│   └── study_plan_service/
├── ai/                  # AI orchestration
│   ├── llm_client.py       # Provider-abstracted LLM interface
│   ├── prompts/             # Prompt templates
│   ├── workflows/           # LangGraph workflows (session orchestration)
│   └── tools/               # LLM tool definitions
├── db/                  # Database
│   ├── models.py            # SQLAlchemy models
│   ├── repositories/        # Data access layer
│   └── migrations/          # Alembic migrations
├── workers/             # Celery tasks
│   ├── document_processor.py
│   ├── embedding_generator.py
│   ├── study_plan_recalculator.py
│   └── analytics_aggregator.py
└── integrations/        # External service clients
    ├── supabase.py
    ├── qdrant.py
    ├── s3.py
    └── redis.py
```

### 2.3 Learning Engine

**Responsibility:** Orchestrate the adaptive learning loop within a session. This is the core product differentiator.

- Implemented as a **LangGraph stateful workflow** for the session state machine.
- Maintains session state in Redis (for fast access) with PostgreSQL persistence.
- Calls Tutor, Assessment Engine, and Student Model as sub-components.

See [LEARNING_ENGINE.md](./LEARNING_ENGINE.md) for detailed design.

### 2.4 Content / Retrieval System

**Responsibility:** Ingest, process, chunk, embed, store, and retrieve learning material.

**Pipeline:**
```
Upload → S3 Storage → Celery Task → Extract Text (PDF/OCR)
  → Chunk (semantic chunking) → Extract Concepts (LLM)
  → Generate Embeddings → Store in Qdrant
  → Link chunks to Concepts in PostgreSQL
```

**Retrieval:**
- Hybrid search: vector similarity (Qdrant) + keyword (PostgreSQL full-text) + concept graph traversal.
- Re-ranking with cross-encoder or LLM-based relevance scoring.

### 2.5 Student Model

**Responsibility:** Maintain and query the student's knowledge state.

- Stores `StudentConceptMastery` records in PostgreSQL.
- Caches hot mastery data in Redis for low-latency access during sessions.
- Exposes methods: `get_mastery(concept_id)`, `update_mastery(concept_id, evidence)`, `get_weakest_concepts(n)`, `get_due_reviews()`.
- Mastery update algorithm: Bayesian Knowledge Tracing (BKT) or a simplified ELO-like update.

### 2.6 Assessment Engine

**Responsibility:** Generate questions, evaluate answers, detect misconceptions.

- Question generation: LLM-driven, constrained by concept + difficulty + question type.
- Answer evaluation: LLM-driven with structured output (correctness, explanation, misconception detection).
- Difficulty calibration: based on student mastery + concept difficulty + historical performance.

### 2.7 Background Processing System

**Responsibility:** Asynchronous, potentially long-running tasks.

| Task | Trigger | Worker |
|---|---|---|
| Document ingestion | File upload | Celery |
| Embedding generation | Document processed | Celery |
| Concept extraction | Document processed | Celery |
| Study plan recalculation | Session end, goal change | Celery |
| Mastery decay | Scheduled (daily) | Celery Beat |
| Analytics aggregation | Scheduled (hourly) | Celery Beat |

---

## 3. Data Flow — Key Scenarios

### 3.1 Document Upload & Processing

```
Student ──upload──▶ Frontend ──presigned URL──▶ S3
                    Frontend ──notify──▶ API ──enqueue──▶ Celery
                                                          │
                    ┌─────────────────────────────────────┘
                    ▼
              Extract text (PyPDF2 / Tesseract)
                    │
              Semantic chunking
                    │
              Concept extraction (LLM)  ──▶ PostgreSQL (Concept, ConceptRelationship)
                    │
              Embedding generation      ──▶ Qdrant (DocumentSection vectors)
                    │
              Link chunks to concepts   ──▶ PostgreSQL (DocumentSection ↔ Concept)
                    │
              Update document status    ──▶ PostgreSQL (Document.status = 'ready')
                    │
              Notify frontend           ──▶ WebSocket / polling
```

### 3.2 Learning Session

```
Student ──start session──▶ API
    │
    ▼
Create LearningSession record
    │
    ▼
LangGraph Workflow: INITIALISE
    ├── Load StudentProfile
    ├── Check StudyPlan for due ReviewItems
    ├── Check LearningGoal priorities
    │
    ▼
PLAN_SESSION
    ├── Query StudentConceptMastery (weakest concepts, prerequisites)
    ├── Select target concepts
    ├── Retrieve relevant DocumentSections from Qdrant
    ├── Decide: EXPLAIN vs PRACTICE vs REVIEW
    │
    ▼
─── INTERACTION LOOP ───
    │
    ├── EXPLAIN: Tutor generates explanation (streamed to frontend via SSE)
    │   └── Log SessionEvent
    │
    ├── PRACTICE: Assessment Engine generates question
    │   ├── Student answers
    │   ├── Assessment Engine evaluates
    │   ├── Update StudentConceptMastery
    │   ├── Detect/log misconceptions
    │   └── Log SessionEvent
    │
    ├── REVIEW: Quick recall check on previously learned concept
    │
    └── DECIDE_NEXT: Continue, switch concept, or end
        ├── Check time budget
        ├── Check mastery progress
        └── Check concept queue
    │
    ▼
WRAP_UP
    ├── Generate session summary (LLM)
    ├── Persist all SessionEvents
    ├── Update cumulative stats
    │
    ▼
SCHEDULE_REVIEW
    ├── Compute next review dates (spaced repetition)
    ├── Update/create ReviewItems
    └── Recalculate StudyPlan (may enqueue Celery task)
```

---

## 4. Technology Decisions Summary

| Concern | Choice | Rationale |
|---|---|---|
| Frontend framework | Next.js + React + TypeScript | SSR for initial load, App Router for modern patterns, TypeScript for safety |
| Backend framework | FastAPI + Python | Async-first, excellent for AI/ML workloads, Pydantic integration |
| Primary database | PostgreSQL | Robust relational store, JSON support, full-text search |
| Vector database | Qdrant Cloud | Purpose-built vector search, managed service reduces ops burden |
| Auth | Supabase Auth | Managed auth with JWT, social login, minimal setup |
| Object storage | S3-compatible | Standard interface, works with AWS S3 or MinIO for local dev |
| Cache / broker | Redis | Session state cache, Celery broker, pub/sub for real-time |
| Background jobs | Celery | Mature, battle-tested, good Redis integration |
| AI orchestration | LangGraph | Stateful workflow graphs for the learning session loop |
| LLM interface | Provider-abstracted | OpenAI-compatible interface; model selection via config |
| ORM | SQLAlchemy 2.0 | Async support, mature, well-documented |
| Migrations | Alembic | Standard for SQLAlchemy |
| API docs | OpenAPI (auto-generated) | FastAPI generates OpenAPI spec automatically |

---

## 5. Deployment Architecture (MVP)

```
┌─────────────────────────────────────────┐
│              Deployment                  │
│                                          │
│  ┌─────────┐  ┌──────────┐  ┌────────┐ │
│  │ Next.js  │  │ FastAPI   │  │ Celery │ │
│  │ (Vercel  │  │ (Railway/ │  │ Worker │ │
│  │  or      │  │  Render/  │  │        │ │
│  │  self)   │  │  Fly.io)  │  │        │ │
│  └────┬─────┘  └────┬──────┘  └───┬────┘ │
│       │              │             │      │
│  ┌────▼──────────────▼─────────────▼────┐ │
│  │         Managed Services              │ │
│  │  PostgreSQL (Supabase / Neon)         │ │
│  │  Redis (Upstash / Railway)            │ │
│  │  Qdrant Cloud                         │ │
│  │  S3 (AWS / Cloudflare R2)             │ │
│  │  Supabase Auth                        │ │
│  └──────────────────────────────────────┘ │
└─────────────────────────────────────────┘
```

---

## 6. Cross-Cutting Concerns

### 6.1 Authentication & Authorization
- Supabase Auth issues JWTs.
- FastAPI middleware validates JWT on every request.
- Row-level security: every query filters by `user_id`.
- See [SECURITY_MODEL.md](./SECURITY_MODEL.md).

### 6.2 Error Handling
- Structured error responses: `{ "error": { "code": "...", "message": "...", "details": {...} } }`.
- Global exception handler in FastAPI.
- AI errors (LLM failures, timeouts) have specific fallback paths.

### 6.3 Observability
- Structured logging (JSON) with correlation IDs.
- AI cost and latency tracking via `AIInteraction` records.
- Health check endpoint at `/api/health`.

### 6.4 Configuration
- Environment-based configuration (`pydantic-settings`).
- Secrets via environment variables (never in code).
- Model selection via config, not hard-coded.

### 6.5 Rate Limiting
- Per-user rate limits on API endpoints.
- Per-user daily LLM cost budget to prevent runaway spending.

---

## 7. Key Architectural Constraints

1. **No hard-coded model provider.** All LLM calls go through a provider-abstracted client. Model selection is configuration-driven.
2. **No hard-coded curriculum.** The system derives structure from uploaded material. Subject/course taxonomy is user-defined.
3. **Concept is the atomic unit.** All mastery tracking, assessment, and review scheduling operates at the concept level.
4. **Session state is recoverable.** If a session is interrupted, it can be resumed from the last persisted state.
5. **AI calls are auditable.** Every LLM invocation is logged with inputs, outputs, cost, and purpose.
6. **Background processing is idempotent.** Celery tasks can be safely retried.
