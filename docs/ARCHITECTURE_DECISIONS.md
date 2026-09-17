# Architecture Decision Records — School in a Box

> **Version:** 0.1.0-draft  
> **Last updated:** 2026-09-17  
> **Status:** Pre-implementation / Architecture Phase

---

## ADR-001: Modular Monolith Over Microservices

**Status:** Accepted  
**Date:** 2026-09-17

### Context
We need to choose between a microservices architecture and a monolithic approach for the MVP.

### Decision
Adopt a **modular monolith**: a single deployable backend (FastAPI) with clean internal module boundaries (learning engine, content system, student model, assessment engine).

### Rationale
- **Development velocity.** A single repo and deployment pipeline is faster for a small team.
- **Reduced operational complexity.** No service mesh, no distributed tracing (yet), no inter-service auth.
- **Refactoring safety.** Module boundaries are enforced by convention and import rules; extraction to services is straightforward if needed later.
- **Latency.** In-process calls between modules avoid network hops during the critical learning session loop.

### Consequences
- Must enforce module boundaries via code review and linting (no circular imports between service modules).
- Scaling is vertical or via multiple instances behind a load balancer (stateless API + Redis for shared state).
- If a single module becomes a bottleneck, it can be extracted into a separate service.

---

## ADR-002: LangGraph for Session Orchestration

**Status:** Accepted  
**Date:** 2026-09-17

### Context
The learning session involves a stateful, multi-step workflow with branching logic (explain → practice → evaluate → decide next). We need a mechanism to manage this state machine.

### Decision
Use **LangGraph** for the session orchestration workflow. Use it only where stateful, adaptive workflows provide clear value — not for simple request-response LLM calls.

### Rationale
- LangGraph provides first-class support for stateful graph-based workflows with checkpointing.
- The session state machine maps naturally to a directed graph with conditional edges.
- Built-in support for human-in-the-loop (student responses mid-workflow).
- Avoids reinventing workflow persistence and state management.

### Alternatives Considered
- **Custom state machine**: More control, but requires building persistence, error recovery, and resumability from scratch.
- **LangChain agents**: Too unstructured for a well-defined state machine; agent loops are harder to reason about and test.
- **Temporal/Prefect**: Overkill for this use case; designed for infrastructure workflows, not interactive AI sessions.

### Consequences
- LangGraph is a dependency; its API surface must be abstracted enough that we could replace it.
- Team must learn LangGraph's programming model.
- Only the session orchestration uses LangGraph; other AI calls (question generation, answer evaluation) are direct LLM calls.

---

## ADR-003: Provider-Abstracted LLM Interface

**Status:** Accepted  
**Date:** 2026-09-17

### Context
We need to call LLMs for multiple purposes (tutoring, question generation, answer evaluation, concept extraction). We must not be locked to a single provider.

### Decision
Build a **provider-abstracted LLM client** with an OpenAI-compatible interface. Model selection is **configuration-driven** per task type.

### Design
```python
# Configuration example (env / config file)
LLM_CONFIG = {
    "tutor_explanation": {
        "provider": "openai",
        "model": "gpt-4o",
        "temperature": 0.7,
        "max_tokens": 2000,
    },
    "question_generation": {
        "provider": "openai",
        "model": "gpt-4o-mini",
        "temperature": 0.8,
        "max_tokens": 1000,
    },
    "answer_evaluation": {
        "provider": "openai",
        "model": "gpt-4o-mini",
        "temperature": 0.2,
        "max_tokens": 500,
    },
    "concept_extraction": {
        "provider": "openai",
        "model": "gpt-4o",
        "temperature": 0.3,
        "max_tokens": 2000,
    },
}
```

### Rationale
- Different tasks have different latency/cost/quality tradeoffs.
- New models can be swapped in via config change, no code change.
- Supports future migration to open-source models (Llama, Mistral) or other providers (Anthropic, Google).

### Consequences
- Must maintain a thin abstraction layer over provider SDKs.
- Structured output parsing must be provider-agnostic (use JSON mode or function calling where available, fall back to prompt-based parsing).

---

## ADR-004: PostgreSQL as Primary Data Store

**Status:** Accepted  
**Date:** 2026-09-17

### Context
We need a durable, relational data store for all core entities.

### Decision
Use **PostgreSQL** as the single source of truth for all structured data.

### Rationale
- Relational model fits the domain well (users, concepts, mastery records, sessions).
- JSON/JSONB columns handle semi-structured data (session events, AI traces).
- Full-text search provides a complement to vector search for hybrid retrieval.
- Mature ecosystem (SQLAlchemy, Alembic, managed hosting options).
- Row-level security concepts align with our multi-tenant-by-user model.

### Consequences
- Must design schema carefully to avoid N+1 queries in the mastery/concept domain.
- Large-scale analytics may eventually need a read replica or OLAP solution.

---

## ADR-005: Qdrant for Vector Storage

**Status:** Accepted  
**Date:** 2026-09-17

### Context
We need vector similarity search for semantic retrieval of document chunks and concept matching.

### Decision
Use **Qdrant Cloud** as the managed vector database.

### Alternatives Considered
- **pgvector**: Would reduce infrastructure; rejected because query performance at scale is inferior to purpose-built vector DBs, and we need advanced filtering (by concept, document, subject).
- **Pinecone**: Viable but more expensive and less flexible on self-hosting.
- **Weaviate**: Viable but heavier operational footprint.
- **ChromaDB**: Not production-ready for managed deployment.

### Rationale
- Purpose-built for vector search with excellent filtering support.
- Managed cloud offering reduces operational burden.
- Good Python client library.
- Supports payload-based filtering (filter by subject, concept, document while doing vector search).

### Consequences
- Additional managed service to configure and monitor.
- Must handle Qdrant unavailability gracefully (cache embeddings, degrade to keyword search).

---

## ADR-006: Supabase Auth for Authentication

**Status:** Accepted  
**Date:** 2026-09-17

### Context
We need user authentication with minimal implementation effort.

### Decision
Use **Supabase Auth** for all authentication concerns.

### Rationale
- Provides email/password and OAuth (Google, GitHub) out of the box.
- Issues standard JWTs that FastAPI can verify independently.
- Client SDK integrates cleanly with Next.js.
- Free tier is sufficient for MVP.
- We already use Supabase-hosted PostgreSQL as an option, so auth is a natural add-on.

### Consequences
- Dependency on Supabase as an auth provider.
- User records in our PostgreSQL must be synced with Supabase Auth (via webhook or on-first-login creation).
- If we outgrow Supabase Auth, migrating to a self-hosted solution (e.g., Keycloak) requires JWT format changes.

---

## ADR-007: Concept as the Atomic Unit of Knowledge

**Status:** Accepted  
**Date:** 2026-09-17

### Context
We need to define the granularity at which we track learning progress.

### Decision
The **Concept** is the atomic unit. All mastery tracking, assessment, retrieval, and scheduling operates at the concept level.

### Rationale
- Concepts are small enough to assess individually but large enough to be meaningful.
- A concept graph with prerequisite relationships enables adaptive sequencing.
- Spaced repetition operates naturally at the concept level.
- This aligns with established educational research (Knowledge Components in ITS literature).

### Guidance on Granularity
A concept should be:
- **Assessable**: You can write a question that tests exactly this concept.
- **Teachable**: You can explain this concept in 2–5 minutes.
- **Distinguishable**: It is meaningfully different from sibling concepts.

Examples:
- ✅ "Pythagorean Theorem" — specific, assessable, teachable.
- ✅ "Solving linear equations with one variable" — focused skill.
- ❌ "Algebra" — too broad, not directly assessable.
- ❌ "The fact that 2+2=4" — too granular, not worth tracking independently.

### Consequences
- Concept extraction from documents must produce items at this granularity (LLM prompt engineering required).
- The concept graph can grow large; need efficient querying.
- Some content may not map cleanly to concepts (narrative text, essays); these are linked at a best-effort level.

---

## ADR-008: Spaced Repetition Algorithm

**Status:** Accepted (algorithm choice deferred)  
**Date:** 2026-09-17

### Context
We need a spaced-repetition algorithm to schedule concept reviews.

### Decision
Implement a **pluggable scheduling interface** with SM-2 as the initial algorithm. The interface allows swapping in FSRS or a custom algorithm later.

### Interface
```python
class ReviewScheduler(Protocol):
    def compute_next_review(
        self,
        mastery: StudentConceptMastery,
        performance: float,  # 0.0 = total failure, 1.0 = perfect
    ) -> ReviewSchedule:
        """Returns next review date and updated interval."""
        ...
```

### Rationale
- SM-2 is well-understood, simple to implement, and good enough for MVP.
- FSRS (Free Spaced Repetition Scheduler) is more modern and accurate but adds complexity.
- A pluggable interface lets us A/B test algorithms later.

### Consequences
- Must store enough state per concept-mastery record to support both SM-2 and FSRS (interval, ease factor, repetition count, last review date).

---

## ADR-009: Streaming Responses for Tutor Interactions

**Status:** Accepted  
**Date:** 2026-09-17

### Context
Tutor explanations can be long (500+ words). Waiting for full generation before displaying creates a poor user experience.

### Decision
Stream tutor responses to the frontend using **Server-Sent Events (SSE)** for explanations and **WebSocket** for bidirectional session interactions.

### Rationale
- SSE is simpler than WebSocket for one-directional streaming (server → client).
- WebSocket is needed for the learning session where the student sends responses mid-stream.
- Both are well-supported by Next.js and FastAPI.

### Consequences
- Frontend must handle partial rendering of streamed markdown/text.
- Error handling for interrupted streams must be robust.
- Session state must be maintained server-side (Redis) across multiple WebSocket messages.

---

## ADR-010: Celery for Background Processing

**Status:** Accepted  
**Date:** 2026-09-17

### Context
Document processing, embedding generation, and study plan recalculation are long-running tasks that should not block API responses.

### Decision
Use **Celery** with **Redis** as the message broker for all background processing.

### Alternatives Considered
- **FastAPI BackgroundTasks**: Too simple for reliable, retriable, observable background work.
- **Dramatiq**: Viable but less ecosystem support.
- **Temporal**: Overkill for MVP.
- **ARQ**: Less mature, smaller community.

### Rationale
- Celery is battle-tested and widely used with Python.
- Redis as broker avoids introducing another message queue (RabbitMQ).
- Celery Beat provides cron-like scheduling for periodic tasks.
- Good monitoring via Flower (web UI) or Celery events.

### Consequences
- Must handle task idempotency (document processing can be retried safely).
- Redis must be configured for persistence (AOF) to avoid losing queued tasks on restart.
- Celery worker deployment is a separate process/container.

---

## ADR-011: SQLAlchemy 2.0 with Async Support

**Status:** Accepted  
**Date:** 2026-09-17

### Context
We need an ORM that supports async operations for FastAPI and provides robust migration tooling.

### Decision
Use **SQLAlchemy 2.0** with `asyncpg` for async PostgreSQL access, and **Alembic** for migrations.

### Rationale
- SQLAlchemy 2.0's new-style API is cleaner and supports async natively.
- Alembic auto-generates migrations from model changes.
- Declarative model definitions serve as living documentation of the schema.

### Consequences
- Team must use SQLAlchemy 2.0 style (not legacy 1.x patterns).
- Async session management requires care (session-per-request pattern).
