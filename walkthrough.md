# School in a Box — Phase 1 Deliverable Summary

> Architecture & Contracts Phase Complete

---

## Architecture Diagram

```mermaid
graph TB
    subgraph Client["Client Layer"]
        FE["Next.js + React + TypeScript"]
        FE_AUTH["Supabase Auth SDK"]
        FE_WS["WebSocket / SSE Client"]
    end

    subgraph API["API Layer — FastAPI"]
        AUTH_MW["Auth Middleware<br/>(JWT Verification)"]
        REST["REST Endpoints<br/>(CRUD, Upload, Progress)"]
        WS["WebSocket Endpoint<br/>(Learning Sessions)"]
        RATE["Rate Limiter"]
    end

    subgraph Services["Service Layer"]
        LE["Learning Engine<br/>(LangGraph Orchestrator)"]
        TUTOR["Tutor<br/>(Explanations, Dialogue)"]
        AE["Assessment Engine<br/>(Questions, Evaluation)"]
        SM["Student Model<br/>(Mastery, Misconceptions)"]
        CS["Content Service<br/>(Retrieval, Search)"]
        SPS["Study Plan Service<br/>(Scheduling, Review)"]
    end

    subgraph AI["AI Infrastructure"]
        LLM_CLIENT["LLM Client<br/>(Provider Abstraction)"]
        PROMPTS["Prompt Manager<br/>(Templates, Versioning)"]
        COST["Cost Tracker<br/>(AIInteraction Logging)"]
    end

    subgraph Background["Background Processing"]
        CELERY["Celery Workers"]
        BEAT["Celery Beat<br/>(Scheduled Tasks)"]
    end

    subgraph Data["Data & Infrastructure"]
        PG["PostgreSQL<br/>(Entities, Mastery, Sessions)"]
        QD["Qdrant Cloud<br/>(Document Embeddings)"]
        REDIS["Redis<br/>(Cache, Broker, Session State)"]
        S3["S3-Compatible<br/>(Document Storage)"]
        SUPA["Supabase Auth<br/>(Identity Provider)"]
    end

    FE --> AUTH_MW
    FE_AUTH --> SUPA
    FE_WS --> WS
    FE --> REST

    AUTH_MW --> REST
    AUTH_MW --> WS
    REST --> RATE

    REST --> LE
    REST --> CS
    REST --> SM
    REST --> SPS
    WS --> LE

    LE --> TUTOR
    LE --> AE
    LE --> SM
    LE --> CS

    TUTOR --> LLM_CLIENT
    AE --> LLM_CLIENT
    CS --> LLM_CLIENT
    LLM_CLIENT --> COST
    LLM_CLIENT --> PROMPTS

    CELERY --> CS
    CELERY --> LLM_CLIENT
    CELERY --> SPS
    BEAT --> CELERY
    CELERY --> REDIS

    SM --> PG
    SM --> REDIS
    CS --> QD
    CS --> PG
    CS --> S3
    LE --> REDIS
    SPS --> PG
    AE --> PG
    COST --> PG
```

---

## Domain Model Diagram

```mermaid
graph LR
    subgraph Identity
        User --> StudentProfile
    end

    subgraph Curriculum
        Subject --> Course --> Chapter --> Section
    end

    subgraph Knowledge["Knowledge Graph"]
        Concept --- ConceptRelationship
        Concept --> StudentConceptMastery
        Concept --> Misconception --> StudentMisconception
    end

    subgraph Content
        Document --> DocumentSection
        DocumentSection -.-> Concept
    end

    subgraph Sessions
        LearningSession --> SessionEvent
        LearningGoal --> LearningSession
    end

    subgraph Assessment
        Question --> QuestionAttempt
        Concept --> Question
    end

    subgraph Scheduling
        StudyPlan --> ReviewItem
        ReviewItem -.-> Concept
    end

    subgraph Observability
        AITrace --> AIInteraction
    end

    User --> Subject
    User --> Document
    User --> LearningGoal
    User --> LearningSession
    User --> StudyPlan
    User --> StudentConceptMastery
    Section -.-> Concept
```

---

## Data Flow Diagram — Learning Session

```mermaid
sequenceDiagram
    participant S as Student
    participant FE as Frontend
    participant API as FastAPI
    participant LE as Learning Engine
    participant SM as Student Model
    participant CS as Content Service
    participant AE as Assessment Engine
    participant TU as Tutor
    participant LLM as LLM Provider
    participant DB as PostgreSQL
    participant RD as Redis
    participant QD as Qdrant

    S->>FE: Start Session
    FE->>API: POST /sessions
    API->>DB: Create LearningSession
    API->>LE: Init workflow

    LE->>SM: Get mastery snapshot
    SM->>DB: Query StudentConceptMastery
    SM->>RD: Cache mastery
    SM-->>LE: Mastery data

    LE->>LE: Select concepts (priority + prerequisites)
    LE->>CS: Retrieve content for concept
    CS->>QD: Vector search
    CS->>DB: Keyword + concept-linked search
    CS-->>LE: Relevant content chunks

    LE->>LE: Decide action: EXPLAIN

    LE->>TU: Generate explanation
    TU->>LLM: Prompt (concept + content + student profile)
    LLM-->>TU: Streamed response
    TU-->>FE: SSE stream (explanation)
    FE-->>S: Display explanation

    S->>FE: "I understand"
    FE->>API: student_acknowledge (WebSocket)
    API->>LE: Resume workflow

    LE->>LE: Decide action: PRACTICE

    LE->>AE: Generate question
    AE->>LLM: Prompt (concept + difficulty + type)
    LLM-->>AE: Question JSON
    AE-->>FE: Question displayed
    FE-->>S: Show question

    S->>FE: Submit answer
    FE->>API: student_response (WebSocket)
    API->>AE: Evaluate answer
    AE->>LLM: Prompt (question + answer + concept)
    LLM-->>AE: Evaluation JSON
    AE-->>LE: Evaluation result

    LE->>SM: Update mastery
    SM->>DB: Write StudentConceptMastery
    SM->>RD: Update cache
    SM-->>LE: Updated mastery

    LE-->>FE: Evaluation + mastery update
    FE-->>S: Show feedback

    LE->>LE: Decide next: WRAP_UP

    LE->>LLM: Generate session summary
    LLM-->>LE: Summary text
    LE->>DB: Persist session events + summary

    LE->>SM: Schedule reviews (SM-2)
    SM->>DB: Create/update ReviewItems
    LE-->>FE: Session summary
    FE-->>S: Show summary
```

---

## AI Workflow Diagram

```mermaid
stateDiagram-v2
    [*] --> INITIALISE
    INITIALISE --> PLAN_SESSION : objective identified

    PLAN_SESSION --> EXPLAIN : action = teach
    PLAN_SESSION --> PRACTICE : action = quiz
    PLAN_SESSION --> REVIEW : action = review

    EXPLAIN --> EVALUATE_RESPONSE : student responds
    EXPLAIN --> PRACTICE : follow-up check
    PRACTICE --> EVALUATE_RESPONSE : answer received
    REVIEW --> EVALUATE_RESPONSE : answer received

    EVALUATE_RESPONSE --> UPDATE_MASTERY : evaluation complete

    UPDATE_MASTERY --> DECIDE_NEXT : mastery persisted

    DECIDE_NEXT --> EXPLAIN : needs teaching
    DECIDE_NEXT --> PRACTICE : needs practice
    DECIDE_NEXT --> REVIEW : needs review
    DECIDE_NEXT --> WRAP_UP : session complete

    WRAP_UP --> SCHEDULE_REVIEW : summary generated
    SCHEDULE_REVIEW --> [*] : reviews scheduled
```

---

## Unresolved Product Assumptions

| # | Assumption | Impact | Proposed Default | Decision Deadline |
|---|---|---|---|---|
| 1 | **Concept granularity** — How fine-grained should AI extraction be? | Affects extraction prompts, mastery tracking precision | "Assessable and teachable in 2–5 minutes" | Phase 3 start |
| 2 | **Mastery "learned" threshold** — 0.80 or different? | Affects when concepts transition to review mode | 0.80 | Phase 4 start |
| 3 | **Session duration** — Fixed or student-adjustable? | Affects UX, fatigue management, cost | Default 30 min, adjustable 10–60 min | Phase 5 start |
| 4 | **Content licensing** — Track copyright of uploaded material? | Legal exposure | No; ToS puts responsibility on student | Phase 3 start |
| 5 | **LLM failure fallback** — Graceful degradation or hard stop? | Reliability during outages | End session with message; no degraded mode in MVP | Phase 4 start |
| 6 | **Pre-built concept graphs** — Seed common subjects? | Cold-start quality for new users | No; derive everything from uploaded material | Phase 3 start |
| 7 | **Multi-document concept merging** — How to deduplicate concepts from different uploads? | Concept graph quality | LLM-assisted deduplication on extraction | Phase 3 |
| 8 | **Cost per session target** — Is $0.10 average realistic? | Model selection, session length | Target $0.10; monitor and adjust model/prompt | Phase 4 |
| 9 | **Question persistence** — Store and reuse generated questions? | Quality consistency vs. freshness | Store and reuse; allow regeneration | Phase 4 |
| 10 | **Mastery decay aggressiveness** — How fast does mastery fade? | Review scheduling pressure | Conservative: `ease_factor × 10` stability constant | Phase 6 |

---

## Proposed Repository Structure

See [DEVELOPMENT_PHASES.md → Proposed Repository Structure](file:///e:/PrepBud/docs/DEVELOPMENT_PHASES.md) for the full tree.

**Summary:**
- `docs/` — 11 architecture documents (this phase)
- `backend/` — FastAPI app with modular service architecture
- `frontend/` — Next.js App Router with TypeScript
- `docker-compose.yml` — Local dev infrastructure
- `.github/workflows/` — CI/CD pipelines

---

## Phase 2 Acceptance Criteria

| ID | Criterion | Verification |
|---|---|---|
| **AC-2.1** | User can sign up via Supabase Auth and `User` + `StudentProfile` records are created in PostgreSQL on first API call | Integration test |
| **AC-2.2** | All CRUD endpoints return correct data, scoped to the authenticated user | Integration test |
| **AC-2.3** | Accessing another user's resources returns 404 (not 403) | Integration test |
| **AC-2.4** | Requests without a valid JWT return 401 | Integration test |
| **AC-2.5** | Database schema matches `DATA_MODEL.md` — all tables created via Alembic migration | Migration run + schema comparison |
| **AC-2.6** | `GET /health` returns service status including database connectivity | Manual + integration test |
| **AC-2.7** | Frontend displays login page, authenticates, and renders a dashboard skeleton | E2E test / manual |
| **AC-2.8** | Docker Compose starts all local dependencies with a single command | Manual verification |
| **AC-2.9** | CI pipeline passes: linting, type checking, and all tests green | CI run |
| **AC-2.10** | Test coverage for backend unit + integration tests ≥ 80% | Coverage report |

---

## Documents Created

| Document | Path | Purpose |
|---|---|---|
| Product Requirements | [PRODUCT_REQUIREMENTS.md](file:///e:/PrepBud/docs/PRODUCT_REQUIREMENTS.md) | Vision, actors, entities, user stories, MVP scope |
| Architecture | [ARCHITECTURE.md](file:///e:/PrepBud/docs/ARCHITECTURE.md) | System components, data flows, tech stack |
| Architecture Decisions | [ARCHITECTURE_DECISIONS.md](file:///e:/PrepBud/docs/ARCHITECTURE_DECISIONS.md) | 11 ADRs with rationale |
| Domain Model | [DOMAIN_MODEL.md](file:///e:/PrepBud/docs/DOMAIN_MODEL.md) | Entities, aggregates, lifecycles, domain rules |
| AI System Design | [AI_SYSTEM_DESIGN.md](file:///e:/PrepBud/docs/AI_SYSTEM_DESIGN.md) | LLM architecture, tutor, assessment, safety |
| Security Model | [SECURITY_MODEL.md](file:///e:/PrepBud/docs/SECURITY_MODEL.md) | Auth, authorization, data security, threats |
| Data Model | [DATA_MODEL.md](file:///e:/PrepBud/docs/DATA_MODEL.md) | PostgreSQL schema, Qdrant, Redis, queries |
| Learning Engine | [LEARNING_ENGINE.md](file:///e:/PrepBud/docs/LEARNING_ENGINE.md) | Mastery algorithms, concept selection, scheduling |
| API Contract | [API_CONTRACT.md](file:///e:/PrepBud/docs/API_CONTRACT.md) | REST + WebSocket endpoints, message protocol |
| Test Strategy | [TEST_STRATEGY.md](file:///e:/PrepBud/docs/TEST_STRATEGY.md) | Test pyramid, AI testing, CI pipeline |
| Development Phases | [DEVELOPMENT_PHASES.md](file:///e:/PrepBud/docs/DEVELOPMENT_PHASES.md) | 8 phases with deliverables and acceptance criteria |
