# School in a Box — Backend

FastAPI service (Python 3.12, async SQLAlchemy 2.0, Alembic). See the repository
root `README.md` for the full local-development workflow and `../docs/` for the
architecture and contracts.

```bash
uv sync                                   # install deps (incl. dev group)
uv run alembic upgrade head               # apply migrations
uv run uvicorn app.main:app --reload      # http://localhost:8000/docs
uv run celery -A app.workers.celery_app worker -l info   # add --pool=solo on Windows
uv run pytest                             # unit + integration (needs Docker)
uv run ruff check . && uv run ruff format --check . && uv run mypy app
```

Layout:

```text
app/api/            routers (documents, concepts + /search, subjects, profile, auth, health)
app/ai/             LLM client (providers/openai.py = any OpenAI-compatible API), prompts/*.md,
                    cost tracking (ai_traces / ai_interactions), structured-output parsing
app/services/content/  text_extractor → chunker → concept_extractor → concept_graph
                    → document_processor (pipeline) → indexer (Qdrant); retriever (hybrid search)
app/services/student_model/  BKT mastery + decay + Redis cache, SM-2 scheduling, misconceptions
app/services/assessment/     difficulty calibration, question generation (stored + reused), grading
app/services/tutor/          context assembly (profile, prerequisites, material, history), streaming
app/services/learning_engine/  concept selection, LangGraph orchestrator, session manager (turns,
                    Redis + PostgreSQL checkpoints, recovery)
app/workers/        Celery app, document + re-index tasks, worker runtime wiring
app/integrations/   redis, s3 (boto3), qdrant
```

Tests use a fake LLM (`tests/fakes.py`), moto for S3 and in-memory Qdrant, against real
PostgreSQL and Redis. `LIVE_LLM_TESTS=1 LLM_API_KEY=… uv run pytest tests/ai` also runs the
opt-in prompt check against a real provider.
