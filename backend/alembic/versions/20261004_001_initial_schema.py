"""Initial schema — all 23 tables from docs/DATA_MODEL.md §3.

Hand-written so the DDL matches DATA_MODEL.md verbatim (extension, GIN full-text
indexes, CHECK constraint, `updated_at` triggers). Model parity is enforced by
tests/integration/test_migrations.py.

Revision ID: 20261004_001
Revises:
Create Date: 2026-10-04
"""

from collections.abc import Sequence

from alembic import op

revision: str = "20261004_001"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# Tables carrying `updated_at` (DATA_MODEL §1: "Mutable tables include updated_at
# ... with a trigger").
MUTABLE_TABLES = (
    "users",
    "student_profiles",
    "subjects",
    "courses",
    "chapters",
    "sections",
    "concepts",
    "documents",
    "learning_goals",
    "learning_sessions",
    "student_misconceptions",
    "student_concept_mastery",
    "study_plans",
    "review_items",
)

# Creation order respects foreign-key dependencies.
TABLES_DDL: tuple[str, ...] = (
    # 3.1 users
    """
    CREATE TABLE users (
        id              UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
        auth_id         TEXT UNIQUE NOT NULL,
        email           TEXT UNIQUE NOT NULL,
        display_name    TEXT,
        avatar_url      TEXT,
        is_active       BOOLEAN DEFAULT TRUE,
        created_at      TIMESTAMPTZ DEFAULT NOW(),
        updated_at      TIMESTAMPTZ DEFAULT NOW()
    );
    CREATE INDEX idx_users_auth_id ON users(auth_id);
    """,
    # 3.2 student_profiles
    """
    CREATE TABLE student_profiles (
        id                UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
        user_id           UUID UNIQUE NOT NULL REFERENCES users(id) ON DELETE CASCADE,
        grade_level       TEXT,
        difficulty_band   TEXT DEFAULT 'intermediate',
        language          TEXT DEFAULT 'en',
        timezone          TEXT DEFAULT 'UTC',
        preferences       JSONB DEFAULT '{}',
        cumulative_stats  JSONB DEFAULT '{}',
        onboarding_state  TEXT DEFAULT 'new',
        created_at        TIMESTAMPTZ DEFAULT NOW(),
        updated_at        TIMESTAMPTZ DEFAULT NOW()
    );
    """,
    # 3.3 subjects
    """
    CREATE TABLE subjects (
        id          UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
        user_id     UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
        name        TEXT NOT NULL,
        description TEXT,
        icon        TEXT,
        created_at  TIMESTAMPTZ DEFAULT NOW(),
        updated_at  TIMESTAMPTZ DEFAULT NOW(),
        UNIQUE(user_id, name)
    );
    CREATE INDEX idx_subjects_user_id ON subjects(user_id);
    """,
    # 3.4 courses
    """
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
    """,
    # 3.5 chapters
    """
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
    """,
    # 3.6 sections
    """
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
    """,
    # 3.7 concepts
    """
    CREATE TABLE concepts (
        id                  UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
        user_id             UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
        name                TEXT NOT NULL,
        description         TEXT,
        difficulty_estimate FLOAT DEFAULT 0.5,
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
    CREATE INDEX idx_concepts_name_fts ON concepts
        USING gin(to_tsvector('english', name || ' ' || COALESCE(description, '')));
    """,
    # 3.8 concept_relationships
    """
    CREATE TABLE concept_relationships (
        id                  UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
        source_concept_id   UUID NOT NULL REFERENCES concepts(id) ON DELETE CASCADE,
        target_concept_id   UUID NOT NULL REFERENCES concepts(id) ON DELETE CASCADE,
        relationship_type   TEXT NOT NULL,
        strength            FLOAT DEFAULT 1.0,
        created_at          TIMESTAMPTZ DEFAULT NOW(),
        UNIQUE(source_concept_id, target_concept_id, relationship_type),
        CHECK(source_concept_id != target_concept_id)
    );
    CREATE INDEX idx_concept_rel_source ON concept_relationships(source_concept_id);
    CREATE INDEX idx_concept_rel_target ON concept_relationships(target_concept_id);
    """,
    # 3.9 documents
    """
    CREATE TABLE documents (
        id                  UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
        user_id             UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
        title               TEXT NOT NULL,
        source_filename     TEXT NOT NULL,
        mime_type           TEXT NOT NULL,
        file_size_bytes     BIGINT,
        s3_key              TEXT NOT NULL,
        processing_status   TEXT DEFAULT 'pending',
        processing_metadata JSONB DEFAULT '{}',
        subject_id          UUID REFERENCES subjects(id) ON DELETE SET NULL,
        course_id           UUID REFERENCES courses(id) ON DELETE SET NULL,
        uploaded_at         TIMESTAMPTZ DEFAULT NOW(),
        processed_at        TIMESTAMPTZ,
        created_at          TIMESTAMPTZ DEFAULT NOW(),
        updated_at          TIMESTAMPTZ DEFAULT NOW()
    );
    CREATE INDEX idx_documents_user_id ON documents(user_id);
    CREATE INDEX idx_documents_status ON documents(processing_status);
    """,
    # 3.10 document_sections
    """
    CREATE TABLE document_sections (
        id              UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
        document_id     UUID NOT NULL REFERENCES documents(id) ON DELETE CASCADE,
        section_index   INTEGER NOT NULL,
        content         TEXT NOT NULL,
        page_numbers    INTEGER[],
        heading         TEXT,
        embedding_id    TEXT,
        token_count     INTEGER,
        metadata        JSONB DEFAULT '{}',
        created_at      TIMESTAMPTZ DEFAULT NOW()
    );
    CREATE INDEX idx_doc_sections_document_id ON document_sections(document_id);
    CREATE INDEX idx_doc_sections_embedding_id ON document_sections(embedding_id);
    CREATE INDEX idx_doc_sections_content_fts ON document_sections
        USING gin(to_tsvector('english', content));
    """,
    # 3.11 document_section_concepts
    """
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
    """,
    # 3.12 learning_goals
    """
    CREATE TABLE learning_goals (
        id              UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
        user_id         UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
        title           TEXT NOT NULL,
        description     TEXT,
        goal_type       TEXT DEFAULT 'mastery',
        target_date     DATE,
        status          TEXT DEFAULT 'active',
        subject_id      UUID REFERENCES subjects(id) ON DELETE SET NULL,
        course_id       UUID REFERENCES courses(id) ON DELETE SET NULL,
        target_concepts UUID[],
        metadata        JSONB DEFAULT '{}',
        created_at      TIMESTAMPTZ DEFAULT NOW(),
        updated_at      TIMESTAMPTZ DEFAULT NOW()
    );
    CREATE INDEX idx_learning_goals_user_id ON learning_goals(user_id);
    CREATE INDEX idx_learning_goals_status ON learning_goals(status);
    """,
    # 3.13 learning_sessions
    """
    CREATE TABLE learning_sessions (
        id                  UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
        user_id             UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
        learning_goal_id    UUID REFERENCES learning_goals(id) ON DELETE SET NULL,
        session_type        TEXT NOT NULL,
        status              TEXT DEFAULT 'initialising',
        objective           JSONB DEFAULT '{}',
        summary             JSONB,
        concepts_covered    UUID[],
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
    """,
    # 3.14 session_events
    """
    CREATE TABLE session_events (
        id              UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
        session_id      UUID NOT NULL REFERENCES learning_sessions(id) ON DELETE CASCADE,
        event_index     INTEGER NOT NULL,
        event_type      TEXT NOT NULL,
        concept_id      UUID REFERENCES concepts(id) ON DELETE SET NULL,
        payload         JSONB NOT NULL DEFAULT '{}',
        created_at      TIMESTAMPTZ DEFAULT NOW()
    );
    CREATE INDEX idx_session_events_session_id ON session_events(session_id);
    CREATE INDEX idx_session_events_type ON session_events(event_type);
    """,
    # 3.15 questions
    """
    CREATE TABLE questions (
        id              UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
        user_id         UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
        concept_id      UUID NOT NULL REFERENCES concepts(id) ON DELETE CASCADE,
        question_type   TEXT NOT NULL,
        difficulty      FLOAT NOT NULL DEFAULT 0.5,
        content         TEXT NOT NULL,
        options         JSONB,
        correct_answer  JSONB NOT NULL,
        explanation     TEXT,
        hints           TEXT[],
        source_type     TEXT DEFAULT 'generated',
        metadata        JSONB DEFAULT '{}',
        created_at      TIMESTAMPTZ DEFAULT NOW()
    );
    CREATE INDEX idx_questions_user_id ON questions(user_id);
    CREATE INDEX idx_questions_concept_id ON questions(concept_id);
    CREATE INDEX idx_questions_difficulty ON questions(difficulty);
    """,
    # 3.16 question_attempts
    """
    CREATE TABLE question_attempts (
        id                      UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
        question_id             UUID NOT NULL REFERENCES questions(id) ON DELETE CASCADE,
        user_id                 UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
        session_id              UUID REFERENCES learning_sessions(id) ON DELETE SET NULL,
        student_response        TEXT NOT NULL,
        is_correct              BOOLEAN NOT NULL,
        score                   FLOAT NOT NULL DEFAULT 0.0,
        ai_evaluation           JSONB DEFAULT '{}',
        misconceptions_detected JSONB DEFAULT '[]',
        time_taken_seconds      INTEGER,
        mastery_delta           FLOAT DEFAULT 0.0,
        attempted_at            TIMESTAMPTZ DEFAULT NOW()
    );
    CREATE INDEX idx_attempts_user_id ON question_attempts(user_id);
    CREATE INDEX idx_attempts_question_id ON question_attempts(question_id);
    CREATE INDEX idx_attempts_session_id ON question_attempts(session_id);
    """,
    # 3.17 misconceptions
    """
    CREATE TABLE misconceptions (
        id                  UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
        concept_id          UUID NOT NULL REFERENCES concepts(id) ON DELETE CASCADE,
        name                TEXT NOT NULL,
        description         TEXT NOT NULL,
        indicators          JSONB DEFAULT '[]',
        remediation_hints   JSONB DEFAULT '[]',
        source_type         TEXT DEFAULT 'detected',
        created_at          TIMESTAMPTZ DEFAULT NOW()
    );
    CREATE INDEX idx_misconceptions_concept_id ON misconceptions(concept_id);
    """,
    # 3.18 student_misconceptions
    """
    CREATE TABLE student_misconceptions (
        id                  UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
        user_id             UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
        misconception_id    UUID NOT NULL REFERENCES misconceptions(id) ON DELETE CASCADE,
        status              TEXT DEFAULT 'active',
        evidence            JSONB DEFAULT '[]',
        occurrence_count    INTEGER DEFAULT 1,
        detected_at         TIMESTAMPTZ DEFAULT NOW(),
        resolved_at         TIMESTAMPTZ,
        created_at          TIMESTAMPTZ DEFAULT NOW(),
        updated_at          TIMESTAMPTZ DEFAULT NOW()
    );
    CREATE INDEX idx_student_misc_user ON student_misconceptions(user_id);
    CREATE INDEX idx_student_misc_status ON student_misconceptions(status);
    """,
    # 3.19 student_concept_mastery
    """
    CREATE TABLE student_concept_mastery (
        id                  UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
        user_id             UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
        concept_id          UUID NOT NULL REFERENCES concepts(id) ON DELETE CASCADE,
        mastery_level       FLOAT DEFAULT 0.0,
        confidence          FLOAT DEFAULT 0.0,
        attempt_count       INTEGER DEFAULT 0,
        correct_count       INTEGER DEFAULT 0,
        streak              INTEGER DEFAULT 0,
        ease_factor         FLOAT DEFAULT 2.5,
        interval_days       FLOAT DEFAULT 1.0,
        repetition_count    INTEGER DEFAULT 0,
        last_assessed_at    TIMESTAMPTZ,
        next_review_at      TIMESTAMPTZ,
        history             JSONB DEFAULT '[]',
        created_at          TIMESTAMPTZ DEFAULT NOW(),
        updated_at          TIMESTAMPTZ DEFAULT NOW(),
        UNIQUE(user_id, concept_id)
    );
    CREATE INDEX idx_mastery_user_id ON student_concept_mastery(user_id);
    CREATE INDEX idx_mastery_concept_id ON student_concept_mastery(concept_id);
    CREATE INDEX idx_mastery_next_review ON student_concept_mastery(next_review_at);
    CREATE INDEX idx_mastery_level ON student_concept_mastery(mastery_level);
    """,
    # 3.20 study_plans
    """
    CREATE TABLE study_plans (
        id                      UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
        user_id                 UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
        learning_goal_id        UUID REFERENCES learning_goals(id) ON DELETE SET NULL,
        status                  TEXT DEFAULT 'active',
        generation_metadata     JSONB DEFAULT '{}',
        generated_at            TIMESTAMPTZ DEFAULT NOW(),
        valid_until             TIMESTAMPTZ,
        created_at              TIMESTAMPTZ DEFAULT NOW(),
        updated_at              TIMESTAMPTZ DEFAULT NOW()
    );
    CREATE INDEX idx_study_plans_user_id ON study_plans(user_id);
    CREATE INDEX idx_study_plans_status ON study_plans(status);
    """,
    # 3.21 review_items
    """
    CREATE TABLE review_items (
        id              UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
        study_plan_id   UUID NOT NULL REFERENCES study_plans(id) ON DELETE CASCADE,
        concept_id      UUID NOT NULL REFERENCES concepts(id) ON DELETE CASCADE,
        scheduled_date  DATE NOT NULL,
        priority        FLOAT DEFAULT 0.5,
        status          TEXT DEFAULT 'pending',
        completed_at    TIMESTAMPTZ,
        created_at      TIMESTAMPTZ DEFAULT NOW(),
        updated_at      TIMESTAMPTZ DEFAULT NOW()
    );
    CREATE INDEX idx_review_items_plan ON review_items(study_plan_id);
    CREATE INDEX idx_review_items_concept ON review_items(concept_id);
    CREATE INDEX idx_review_items_date ON review_items(scheduled_date);
    CREATE INDEX idx_review_items_status ON review_items(status);
    """,
    # 3.22 ai_traces
    """
    CREATE TABLE ai_traces (
        id              UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
        user_id         UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
        session_id      UUID REFERENCES learning_sessions(id) ON DELETE SET NULL,
        operation       TEXT NOT NULL,
        status          TEXT DEFAULT 'started',
        total_tokens    INTEGER DEFAULT 0,
        total_cost      FLOAT DEFAULT 0.0,
        started_at      TIMESTAMPTZ DEFAULT NOW(),
        completed_at    TIMESTAMPTZ,
        metadata        JSONB DEFAULT '{}'
    );
    CREATE INDEX idx_ai_traces_user_id ON ai_traces(user_id);
    CREATE INDEX idx_ai_traces_session_id ON ai_traces(session_id);
    """,
    # 3.23 ai_interactions
    """
    CREATE TABLE ai_interactions (
        id              UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
        trace_id        UUID NOT NULL REFERENCES ai_traces(id) ON DELETE CASCADE,
        user_id         UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
        purpose         TEXT NOT NULL,
        provider        TEXT NOT NULL,
        model           TEXT NOT NULL,
        prompt_hash     TEXT,
        response_hash   TEXT,
        input_tokens    INTEGER NOT NULL DEFAULT 0,
        output_tokens   INTEGER NOT NULL DEFAULT 0,
        cost_estimate   FLOAT DEFAULT 0.0,
        latency_ms      INTEGER DEFAULT 0,
        status          TEXT DEFAULT 'success',
        error_message   TEXT,
        metadata        JSONB DEFAULT '{}',
        created_at      TIMESTAMPTZ DEFAULT NOW()
    );
    CREATE INDEX idx_ai_interactions_trace_id ON ai_interactions(trace_id);
    CREATE INDEX idx_ai_interactions_user_id ON ai_interactions(user_id);
    CREATE INDEX idx_ai_interactions_purpose ON ai_interactions(purpose);
    CREATE INDEX idx_ai_interactions_created ON ai_interactions(created_at);
    """,
)

# Reverse dependency order for downgrade.
TABLE_NAMES: tuple[str, ...] = (
    "users",
    "student_profiles",
    "subjects",
    "courses",
    "chapters",
    "sections",
    "concepts",
    "concept_relationships",
    "documents",
    "document_sections",
    "document_section_concepts",
    "learning_goals",
    "learning_sessions",
    "session_events",
    "questions",
    "question_attempts",
    "misconceptions",
    "student_misconceptions",
    "student_concept_mastery",
    "study_plans",
    "review_items",
    "ai_traces",
    "ai_interactions",
)


def upgrade() -> None:
    op.execute('CREATE EXTENSION IF NOT EXISTS "uuid-ossp"')
    op.execute(
        """
        CREATE OR REPLACE FUNCTION set_updated_at() RETURNS trigger AS $$
        BEGIN
            NEW.updated_at = NOW();
            RETURN NEW;
        END;
        $$ LANGUAGE plpgsql;
        """
    )
    for ddl in TABLES_DDL:
        # asyncpg executes one statement per call.
        for statement in ddl.split(";"):
            if statement.strip():
                op.execute(statement)
    for table in MUTABLE_TABLES:
        op.execute(
            f"CREATE TRIGGER trg_{table}_updated_at BEFORE UPDATE ON {table} "
            "FOR EACH ROW EXECUTE FUNCTION set_updated_at()"
        )


def downgrade() -> None:
    for table in reversed(TABLE_NAMES):
        op.execute(f"DROP TABLE IF EXISTS {table} CASCADE")
    op.execute("DROP FUNCTION IF EXISTS set_updated_at()")
    # The uuid-ossp extension is left in place: it may be shared with other schemas.
