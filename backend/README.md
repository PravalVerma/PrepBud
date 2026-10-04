# School in a Box — Backend

FastAPI service (Python 3.12, async SQLAlchemy 2.0, Alembic). See the repository
root `README.md` for the full local-development workflow and `../docs/` for the
architecture and contracts.

```bash
uv sync                                   # install deps (incl. dev group)
uv run alembic upgrade head               # apply migrations
uv run uvicorn app.main:app --reload      # http://localhost:8000/docs
uv run pytest                             # unit + integration (needs Docker)
uv run ruff check . && uv run ruff format --check . && uv run mypy app
```
