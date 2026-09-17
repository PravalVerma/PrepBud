# Data Model — School in a Box

> **Version:** 0.1.0-draft  
> **Last updated:** 2026-09-17  
> **Status:** Pre-implementation / Architecture Phase

---

## 1. Overview

This document defines the physical database schema for PostgreSQL. It complements the [Domain Model](./DOMAIN_MODEL.md) which defines logical entities and relationships.

**Conventions:**
- All tables use `UUID` primary keys (generated as `uuid_generate_v4()`).
- All tables include `created_at TIMESTAMPTZ DEFAULT NOW()`.
- Mutable tables include `updated_at TIMESTAMPTZ DEFAULT NOW()` with a trigger.
- All foreign keys referencing `users.id` enable cascade delete.
- JSON/JSONB columns are used for semi-structured data that doesn't warrant separate tables.
- All timestamps are stored in UTC.

---

## 2. Schema Diagram

```
users
  ├── student_profiles (1:1)
  ├── subjects (1:N)
  │     └── courses (1:N)
  │           └── chapters (1:N)
  │                 └── sections (1:N)
  ├── concepts (1:N)
  │     ├── concept_relationships (N:M via join)
  │     ├── document_section_concepts (N:M via join)
  │     ├── questions (1:N)
  │     ├── student_concept_mastery (1:N, per user-concept)
  │     ├── misconceptions (1:N)
  │     └── review_items (1:N)
  ├── documents (1:N)
  │     └── document_sections (1:N)
  ├── learning_goals (1:N)
  ├── learning_sessions (1:N)
  │     └── session_events (1:N)
  ├── study_plans (1:N)
  │     └── review_items (1:N)
  ├── question_attempts (1:N)
  ├── student_misconceptions (1:N)
  ├── ai_traces (1:N)
  │     └── ai_interactions (1:N)
  └── (all queries filtered by user_id)
```

---

## 3. Table Definitions

### 3.1 `users`

```sql
CREATE TABLE users (
    id              UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    auth_id         TEXT UNIQUE NOT NULL,          -- Supabase Auth sub claim
    email           TEXT UNIQUE NOT NULL,
    display_name    TEXT,
    avatar_url      TEXT,
    is_active       BOOLEAN DEFAULT TRUE,
    created_at      TIMESTAMPTZ DEFAULT NOW(),
    updated_at      TIMESTAMPTZ DEFAULT NOW()
);

CREATE INDEX idx_users_auth_id ON users(auth_id);
```

### 3.2 `student_profiles`

```sql
CREATE TABLE student_profiles (
    id                UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    user_id           UUID UNIQUE NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    grade_level       TEXT,                         -- e.g., "10th grade", "undergraduate"
    difficulty_band   TEXT DEFAULT 'intermediate',  -- novice | beginner | intermediate | advanced
    language          TEXT DEFAULT 'en',
    timezone          TEXT DEFAULT 'UTC',
    preferences       JSONB DEFAULT '{}',           -- UI prefs, learning style hints
    cumulative_stats  JSONB DEFAULT '{}',           -- total sessions, total time, etc.
    onboarding_state  TEXT DEFAULT 'new',           -- new | profile_set | first_upload | first_session | complete
    created_at        TIMESTAMPTZ DEFAULT NOW(),
    updated_at        TIMESTAMPTZ DEFAULT NOW()
);
```

### 3.3 `subjects`

```sql
CREATE TABLE subjects (
    id          UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    user_id     UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    name        TEXT NOT NULL,
    description TEXT,
    icon        TEXT,                               -- emoji or icon identifier
    created_at  TIMESTAMPTZ DEFAULT NOW(),
    updated_at  TIMESTAMPTZ DEFAULT NOW(),
    UNIQUE(user_id, name)
);

CREATE INDEX idx_subjects_user_id ON subjects(user_id);
```

### 3.4 `courses`

```sql
CREATE TABLE courses (
    id          UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    subject_id  UUID NOT NULL REFERENCES subjects(id) ON DELETE CASCADE,
    name        TEXT NOT NULL,
    description TEXT,
    sort_order  INTEGER DEFAULT 0,
    created_at  TIMESTAMPTZ DEFAULT NOW(),
    updated_at  TIMESTAMPTZ DEFAULT NOW()
);

CREATE INDEX idx_courses_subject_id ON courses(subject_id);
```

### 3.5 `chapters`

```sql
CREATE TABLE chapters (
    id          UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    course_id   UUID NOT NULL REFERENCES courses(id) ON DELETE CASCADE,
    name        TEXT NOT NULL,
    description TEXT,
    sort_order  INTEGER DEFAULT 0,
    created_at  TIMESTAMPTZ DEFAULT NOW(),
    updated_at  TIMESTAMPTZ DEFAULT NOW()
);

CREATE INDEX idx_chapters_course_id ON chapters(course_id);
```

### 3.6 `sections`

```sql
CREATE TABLE sections (
    id          UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    chapter_id  UUID NOT NULL REFERENCES chapters(id) ON DELETE CASCADE,
    name        TEXT NOT NULL,
    description TEXT,
    sort_order  INTEGER DEFAULT 0,
    created_at  TIMESTAMPTZ DEFAULT NOW(),
    updated_at  TIMESTAMPTZ DEFAULT NOW()
);

CREATE INDEX idx_sections_chapter_id ON sections(chapter_id);
```

### 3.7 `concepts`

```sql
CREATE TABLE concepts (
    id                  UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    user_id             UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    name                TEXT NOT NULL,
    description         TEXT,
    difficulty_estimate FLOAT DEFAULT 0.5,          -- 0.0 to 1.0
    section_id          UUID REFERENCES sections(id) ON DELETE SET NULL,
    chapter_id          UUID REFERENCES chapters(id) ON DELETE SET NULL,
    subject_id          UUID REFERENCES subjects(id) ON DELETE SET NULL,
    metadata            JSONB DEFAULT '{}',
    created_at          TIMESTAMPTZ DEFAULT NOW(),
    updated_at          TIMESTAMPTZ DEFAULT NOW()
);

CREATE INDEX idx_concepts_user_id ON concepts(user_id);
CREATE INDEX idx_concepts_section_id ON concepts(section_id);
CREATE INDEX idx_concepts_chapter_id ON concepts(chapter_id);
CREATE INDEX idx_concepts_subject_id ON concepts(subject_id);
-- Full-text search index
CREATE INDEX idx_concepts_name_fts ON concepts USING gin(to_tsvector('english', name || ' ' || COALESCE(description, '')));
```

### 3.8 `concept_relationships`

```sql
CREATE TABLE concept_relationships (
    id                  UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    source_concept_id   UUID NOT NULL REFERENCES concepts(id) ON DELETE CASCADE,
    target_concept_id   UUID NOT NULL REFERENCES concepts(id) ON DELETE CASCADE,
    relationship_type   TEXT NOT NULL,               -- prerequisite | related | generalisation | specialisation
    strength            FLOAT DEFAULT 1.0,           -- 0.0 to 1.0, edge weight
    created_at          TIMESTAMPTZ DEFAULT NOW(),
    UNIQUE(source_concept_id, target_concept_id, relationship_type),
    CHECK(source_concept_id != target_concept_id)
);

CREATE INDEX idx_concept_rel_source ON concept_relationships(source_concept_id);
CREATE INDEX idx_concept_rel_target ON concept_relationships(target_concept_id);
```

### 3.9 `documents`

```sql
CREATE TABLE documents (
    id                  UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    user_id             UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    title               TEXT NOT NULL,
    source_filename     TEXT NOT NULL,
    mime_type           TEXT NOT NULL,
    file_size_bytes     BIGINT,
    s3_key              TEXT NOT NULL,
    processing_status   TEXT DEFAULT 'pending',      -- pending | processing | ready | failed
    processing_metadata JSONB DEFAULT '{}',          -- page count, chunk count, error details
    subject_id          UUID REFERENCES subjects(id) ON DELETE SET NULL,
    course_id           UUID REFERENCES courses(id) ON DELETE SET NULL,
    uploaded_at         TIMESTAMPTZ DEFAULT NOW(),
    processed_at        TIMESTAMPTZ,
    created_at          TIMESTAMPTZ DEFAULT NOW(),
    updated_at          TIMESTAMPTZ DEFAULT NOW()
);

CREATE INDEX idx_documents_user_id ON documents(user_id);
CREATE INDEX idx_documents_status ON documents(processing_status);
```

### 3.10 `document_sections`

```sql
CREATE TABLE document_sections (
    id              UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    document_id     UUID NOT NULL REFERENCES documents(id) ON DELETE CASCADE,
    section_index   INTEGER NOT NULL,
    content         TEXT NOT NULL,
    page_numbers    INTEGER[],                       -- source page(s) in original document
    heading         TEXT,                             -- section heading if extracted
    embedding_id    TEXT,                             -- Qdrant point ID
    token_count     INTEGER,
    metadata        JSONB DEFAULT '{}',
    created_at      TIMESTAMPTZ DEFAULT NOW()
);

CREATE INDEX idx_doc_sections_document_id ON document_sections(document_id);
CREATE INDEX idx_doc_sections_embedding_id ON document_sections(embedding_id);
-- Full-text search
CREATE INDEX idx_doc_sections_content_fts ON document_sections USING gin(to_tsvector('english', content));
```

### 3.11 `document_section_concepts` (Join Table)

```sql
CREATE TABLE document_section_concepts (
    id                  UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    document_section_id UUID NOT NULL REFERENCES document_sections(id) ON DELETE CASCADE,
    concept_id          UUID NOT NULL REFERENCES concepts(id) ON DELETE CASCADE,
    relevance_score     FLOAT DEFAULT 1.0,
    created_at          TIMESTAMPTZ DEFAULT NOW(),
    UNIQUE(document_section_id, concept_id)
);

CREATE INDEX idx_dsc_section ON document_section_concepts(document_section_id);
CREATE INDEX idx_dsc_concept ON document_section_concepts(concept_id);
```

### 3.12 `learning_goals`

```sql
CREATE TABLE learning_goals (
    id              UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    user_id         UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    title           TEXT NOT NULL,
    description     TEXT,
    goal_type       TEXT DEFAULT 'mastery',          -- mastery | deadline | exploration
    target_date     DATE,
    status          TEXT DEFAULT 'active',            -- draft | active | completed | paused | abandoned
    subject_id      UUID REFERENCES subjects(id) ON DELETE SET NULL,
    course_id       UUID REFERENCES courses(id) ON DELETE SET NULL,
    target_concepts UUID[],                          -- array of concept IDs
    metadata        JSONB DEFAULT '{}',
    created_at      TIMESTAMPTZ DEFAULT NOW(),
    updated_at      TIMESTAMPTZ DEFAULT NOW()
);

CREATE INDEX idx_learning_goals_user_id ON learning_goals(user_id);
CREATE INDEX idx_learning_goals_status ON learning_goals(status);
```

### 3.13 `learning_sessions`

```sql
CREATE TABLE learning_sessions (
    id                  UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    user_id             UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    learning_goal_id    UUID REFERENCES learning_goals(id) ON DELETE SET NULL,
    session_type        TEXT NOT NULL,                -- teach | practice | review | mixed
    status              TEXT DEFAULT 'initialising',  -- initialising | active | paused | completed | abandoned
    objective           JSONB DEFAULT '{}',           -- target concepts, session parameters
    summary             JSONB,                        -- generated at end: stats, concepts covered, mastery changes
    concepts_covered    UUID[],                       -- array of concept IDs engaged
    interaction_count   INTEGER DEFAULT 0,
    started_at          TIMESTAMPTZ DEFAULT NOW(),
    ended_at            TIMESTAMPTZ,
    duration_seconds    INTEGER,
    metadata            JSONB DEFAULT '{}',
    created_at          TIMESTAMPTZ DEFAULT NOW(),
    updated_at          TIMESTAMPTZ DEFAULT NOW()
);

CREATE INDEX idx_sessions_user_id ON learning_sessions(user_id);
CREATE INDEX idx_sessions_status ON learning_sessions(status);
CREATE INDEX idx_sessions_started ON learning_sessions(started_at);
```

### 3.14 `session_events`

```sql
CREATE TABLE session_events (
    id              UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    session_id      UUID NOT NULL REFERENCES learning_sessions(id) ON DELETE CASCADE,
    event_index     INTEGER NOT NULL,
    event_type      TEXT NOT NULL,                    -- see event types below
    concept_id      UUID REFERENCES concepts(id) ON DELETE SET NULL,
    payload         JSONB NOT NULL DEFAULT '{}',      -- event-specific data
    created_at      TIMESTAMPTZ DEFAULT NOW()
);

-- Event types:
-- explanation_given, question_asked, answer_received, hint_given,
-- misconception_detected, mastery_updated, concept_changed,
-- session_paused, session_resumed, follow_up_question, follow_up_answer

CREATE INDEX idx_session_events_session_id ON session_events(session_id);
CREATE INDEX idx_session_events_type ON session_events(event_type);
```

### 3.15 `questions`

```sql
CREATE TABLE questions (
    id              UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    user_id         UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    concept_id      UUID NOT NULL REFERENCES concepts(id) ON DELETE CASCADE,
    question_type   TEXT NOT NULL,                    -- mcq | short_answer | true_false | worked_problem | open_ended
    difficulty      FLOAT NOT NULL DEFAULT 0.5,      -- 0.0 to 1.0
    content         TEXT NOT NULL,                    -- question text (markdown)
    options         JSONB,                            -- for MCQ: [{label, text, is_correct}]
    correct_answer  JSONB NOT NULL,                   -- answer representation
    explanation     TEXT,                             -- solution explanation
    hints           TEXT[],                           -- ordered hints
    source_type     TEXT DEFAULT 'generated',         -- generated | templated | imported
    metadata        JSONB DEFAULT '{}',
    created_at      TIMESTAMPTZ DEFAULT NOW()
);

CREATE INDEX idx_questions_user_id ON questions(user_id);
CREATE INDEX idx_questions_concept_id ON questions(concept_id);
CREATE INDEX idx_questions_difficulty ON questions(difficulty);
```

### 3.16 `question_attempts`

```sql
CREATE TABLE question_attempts (
    id                      UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    question_id             UUID NOT NULL REFERENCES questions(id) ON DELETE CASCADE,
    user_id                 UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    session_id              UUID REFERENCES learning_sessions(id) ON DELETE SET NULL,
    student_response        TEXT NOT NULL,
    is_correct              BOOLEAN NOT NULL,
    score                   FLOAT NOT NULL DEFAULT 0.0,  -- 0.0 to 1.0 (partial credit)
    ai_evaluation           JSONB DEFAULT '{}',           -- LLM evaluation details
    misconceptions_detected JSONB DEFAULT '[]',           -- [{misconception_id, confidence}]
    time_taken_seconds      INTEGER,
    mastery_delta           FLOAT DEFAULT 0.0,            -- change in mastery from this attempt
    attempted_at            TIMESTAMPTZ DEFAULT NOW()
);

CREATE INDEX idx_attempts_user_id ON question_attempts(user_id);
CREATE INDEX idx_attempts_question_id ON question_attempts(question_id);
CREATE INDEX idx_attempts_session_id ON question_attempts(session_id);
```

### 3.17 `misconceptions`

```sql
CREATE TABLE misconceptions (
    id                  UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    concept_id          UUID NOT NULL REFERENCES concepts(id) ON DELETE CASCADE,
    name                TEXT NOT NULL,
    description         TEXT NOT NULL,
    indicators          JSONB DEFAULT '[]',           -- patterns that indicate this misconception
    remediation_hints   JSONB DEFAULT '[]',           -- teaching strategies to address it
    source_type         TEXT DEFAULT 'detected',      -- detected | catalogued | imported
    created_at          TIMESTAMPTZ DEFAULT NOW()
);

CREATE INDEX idx_misconceptions_concept_id ON misconceptions(concept_id);
```

### 3.18 `student_misconceptions`

```sql
CREATE TABLE student_misconceptions (
    id                  UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    user_id             UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    misconception_id    UUID NOT NULL REFERENCES misconceptions(id) ON DELETE CASCADE,
    status              TEXT DEFAULT 'active',         -- active | resolved | recurring
    evidence            JSONB DEFAULT '[]',           -- [{attempt_id, description, detected_at}]
    occurrence_count    INTEGER DEFAULT 1,
    detected_at         TIMESTAMPTZ DEFAULT NOW(),
    resolved_at         TIMESTAMPTZ,
    created_at          TIMESTAMPTZ DEFAULT NOW(),
    updated_at          TIMESTAMPTZ DEFAULT NOW()
);

CREATE INDEX idx_student_misc_user ON student_misconceptions(user_id);
CREATE INDEX idx_student_misc_status ON student_misconceptions(status);
```

### 3.19 `student_concept_mastery`

```sql
CREATE TABLE student_concept_mastery (
    id                  UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    user_id             UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    concept_id          UUID NOT NULL REFERENCES concepts(id) ON DELETE CASCADE,
    mastery_level       FLOAT DEFAULT 0.0,            -- 0.0 to 1.0
    confidence          FLOAT DEFAULT 0.0,            -- confidence in the mastery estimate
    attempt_count       INTEGER DEFAULT 0,
    correct_count       INTEGER DEFAULT 0,
    streak              INTEGER DEFAULT 0,            -- consecutive correct answers
    -- Spaced repetition fields (SM-2 compatible)
    ease_factor         FLOAT DEFAULT 2.5,
    interval_days       FLOAT DEFAULT 1.0,
    repetition_count    INTEGER DEFAULT 0,
    last_assessed_at    TIMESTAMPTZ,
    next_review_at      TIMESTAMPTZ,
    history             JSONB DEFAULT '[]',           -- [{date, mastery, event}] recent history
    created_at          TIMESTAMPTZ DEFAULT NOW(),
    updated_at          TIMESTAMPTZ DEFAULT NOW(),
    UNIQUE(user_id, concept_id)
);

CREATE INDEX idx_mastery_user_id ON student_concept_mastery(user_id);
CREATE INDEX idx_mastery_concept_id ON student_concept_mastery(concept_id);
CREATE INDEX idx_mastery_next_review ON student_concept_mastery(next_review_at);
CREATE INDEX idx_mastery_level ON student_concept_mastery(mastery_level);
```

### 3.20 `study_plans`

```sql
CREATE TABLE study_plans (
    id                      UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    user_id                 UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    learning_goal_id        UUID REFERENCES learning_goals(id) ON DELETE SET NULL,
    status                  TEXT DEFAULT 'active',     -- active | superseded | completed
    generation_metadata     JSONB DEFAULT '{}',        -- algorithm used, parameters, timestamp
    generated_at            TIMESTAMPTZ DEFAULT NOW(),
    valid_until             TIMESTAMPTZ,
    created_at              TIMESTAMPTZ DEFAULT NOW(),
    updated_at              TIMESTAMPTZ DEFAULT NOW()
);

CREATE INDEX idx_study_plans_user_id ON study_plans(user_id);
CREATE INDEX idx_study_plans_status ON study_plans(status);
```

### 3.21 `review_items`

```sql
CREATE TABLE review_items (
    id              UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    study_plan_id   UUID NOT NULL REFERENCES study_plans(id) ON DELETE CASCADE,
    concept_id      UUID NOT NULL REFERENCES concepts(id) ON DELETE CASCADE,
    scheduled_date  DATE NOT NULL,
    priority        FLOAT DEFAULT 0.5,                -- 0.0 to 1.0
    status          TEXT DEFAULT 'pending',            -- pending | completed | skipped | overdue
    completed_at    TIMESTAMPTZ,
    created_at      TIMESTAMPTZ DEFAULT NOW(),
    updated_at      TIMESTAMPTZ DEFAULT NOW()
);

CREATE INDEX idx_review_items_plan ON review_items(study_plan_id);
CREATE INDEX idx_review_items_concept ON review_items(concept_id);
CREATE INDEX idx_review_items_date ON review_items(scheduled_date);
CREATE INDEX idx_review_items_status ON review_items(status);
```

### 3.22 `ai_traces`

```sql
CREATE TABLE ai_traces (
    id              UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    user_id         UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    session_id      UUID REFERENCES learning_sessions(id) ON DELETE SET NULL,
    operation       TEXT NOT NULL,                    -- e.g., session_explain, session_practice, document_ingest
    status          TEXT DEFAULT 'started',           -- started | completed | failed
    total_tokens    INTEGER DEFAULT 0,
    total_cost      FLOAT DEFAULT 0.0,
    started_at      TIMESTAMPTZ DEFAULT NOW(),
    completed_at    TIMESTAMPTZ,
    metadata        JSONB DEFAULT '{}'
);

CREATE INDEX idx_ai_traces_user_id ON ai_traces(user_id);
CREATE INDEX idx_ai_traces_session_id ON ai_traces(session_id);
```

### 3.23 `ai_interactions`

```sql
CREATE TABLE ai_interactions (
    id              UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    trace_id        UUID NOT NULL REFERENCES ai_traces(id) ON DELETE CASCADE,
    user_id         UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    purpose         TEXT NOT NULL,                    -- generate_explanation, evaluate_answer, etc.
    provider        TEXT NOT NULL,                    -- openai, anthropic, ollama
    model           TEXT NOT NULL,                    -- gpt-4o, gpt-4o-mini, etc.
    prompt_hash     TEXT,                             -- SHA-256 of the prompt (for dedup/analysis)
    response_hash   TEXT,
    input_tokens    INTEGER NOT NULL DEFAULT 0,
    output_tokens   INTEGER NOT NULL DEFAULT 0,
    cost_estimate   FLOAT DEFAULT 0.0,               -- USD
    latency_ms      INTEGER DEFAULT 0,
    status          TEXT DEFAULT 'success',           -- success | error | timeout
    error_message   TEXT,
    metadata        JSONB DEFAULT '{}',
    created_at      TIMESTAMPTZ DEFAULT NOW()
);

CREATE INDEX idx_ai_interactions_trace_id ON ai_interactions(trace_id);
CREATE INDEX idx_ai_interactions_user_id ON ai_interactions(user_id);
CREATE INDEX idx_ai_interactions_purpose ON ai_interactions(purpose);
CREATE INDEX idx_ai_interactions_created ON ai_interactions(created_at);
```

---

## 4. Qdrant Collections

### 4.1 `document_sections`

```json
{
  "collection_name": "document_sections",
  "vectors": {
    "size": 1536,
    "distance": "Cosine"
  },
  "payload_schema": {
    "user_id": "keyword",
    "document_id": "keyword",
    "subject_id": "keyword",
    "course_id": "keyword",
    "concept_ids": "keyword[]",
    "section_index": "integer",
    "heading": "text",
    "content_preview": "text",
    "token_count": "integer"
  }
}
```

**Filtering:** All vector searches include `user_id` filter to enforce data isolation.

### 4.2 `concepts` (Optional — for concept similarity search)

```json
{
  "collection_name": "concepts",
  "vectors": {
    "size": 1536,
    "distance": "Cosine"
  },
  "payload_schema": {
    "user_id": "keyword",
    "concept_name": "text",
    "subject_id": "keyword",
    "difficulty_estimate": "float"
  }
}
```

---

## 5. Redis Key Patterns

```
# Session state (active learning session)
session:{session_id}:state          → JSON (SessionState)
session:{session_id}:messages       → List of chat messages
session:{session_id}:ttl            → 2 hours

# Mastery cache (hot data during session)
mastery:{user_id}:{concept_id}     → JSON (mastery snapshot)
mastery:{user_id}:{concept_id}:ttl → 30 minutes

# Rate limiting
rate:{user_id}:{endpoint}          → Counter with TTL

# Task status (Celery task tracking for UI)
task:{task_id}:status              → JSON (status, progress, result)
task:{task_id}:ttl                 → 1 hour

# User daily cost tracking
cost:{user_id}:{date}              → Float (accumulated daily cost)
cost:{user_id}:{date}:ttl          → 25 hours
```

---

## 6. Migration Strategy

- **Tool:** Alembic (integrated with SQLAlchemy 2.0).
- **Approach:** Auto-generated migrations from model changes, reviewed before applying.
- **Naming:** `{timestamp}_{description}.py` (e.g., `20260917_001_initial_schema.py`).
- **Environment:** Separate migration runs for dev, staging, production.
- **Rollback:** Every migration includes a `downgrade()` function.

---

## 7. Query Patterns (Critical Paths)

### 7.1 Get Student's Weakest Concepts (for session planning)

```sql
SELECT c.id, c.name, scm.mastery_level, scm.next_review_at
FROM concepts c
JOIN student_concept_mastery scm ON c.id = scm.concept_id
WHERE scm.user_id = :user_id
  AND c.subject_id = :subject_id
ORDER BY scm.mastery_level ASC, scm.next_review_at ASC NULLS FIRST
LIMIT :n;
```

### 7.2 Get Due Review Items

```sql
SELECT ri.*, c.name as concept_name, scm.mastery_level
FROM review_items ri
JOIN concepts c ON ri.concept_id = c.id
JOIN student_concept_mastery scm ON ri.concept_id = scm.concept_id AND scm.user_id = :user_id
JOIN study_plans sp ON ri.study_plan_id = sp.id
WHERE sp.user_id = :user_id
  AND sp.status = 'active'
  AND ri.status = 'pending'
  AND ri.scheduled_date <= :today
ORDER BY ri.priority DESC, ri.scheduled_date ASC;
```

### 7.3 Get Concept with Prerequisites and Mastery

```sql
SELECT 
    c.id, c.name, c.description,
    scm.mastery_level,
    ARRAY_AGG(DISTINCT cr.source_concept_id) FILTER (WHERE cr.relationship_type = 'prerequisite') as prerequisite_ids
FROM concepts c
LEFT JOIN student_concept_mastery scm ON c.id = scm.concept_id AND scm.user_id = :user_id
LEFT JOIN concept_relationships cr ON c.id = cr.target_concept_id
WHERE c.id = :concept_id
GROUP BY c.id, scm.mastery_level;
```

### 7.4 Get Session History with Events

```sql
SELECT ls.*, 
    COUNT(se.id) as event_count,
    ARRAY_AGG(DISTINCT se.event_type) as event_types
FROM learning_sessions ls
LEFT JOIN session_events se ON ls.id = se.session_id
WHERE ls.user_id = :user_id
GROUP BY ls.id
ORDER BY ls.started_at DESC
LIMIT :limit OFFSET :offset;
```
