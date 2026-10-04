# School in a Box — common commands. Run `make help` for the list.
# (On Windows without make, run the commands shown in each recipe directly.)

.DEFAULT_GOAL := help
.PHONY: help up down install migrate dev-backend dev-frontend test test-backend \
        test-frontend test-e2e lint typecheck format check

help: ## Show this help
	@grep -E '^[a-zA-Z_-]+:.*?## ' $(MAKEFILE_LIST) | awk 'BEGIN {FS = ":.*?## "}; {printf "  \033[36m%-15s\033[0m %s\n", $$1, $$2}'

up: ## Start local PostgreSQL + Redis
	docker compose up -d --wait

down: ## Stop local infrastructure
	docker compose down

install: ## Install backend + frontend dependencies
	cd backend && uv sync
	cd frontend && npm install

migrate: ## Apply database migrations
	cd backend && uv run alembic upgrade head

dev-backend: ## Run the API with auto-reload (http://localhost:8000/docs)
	cd backend && uv run uvicorn app.main:app --reload --port 8000

dev-frontend: ## Run the Next.js dev server (http://localhost:3000)
	cd frontend && npm run dev

test: test-backend test-frontend ## Run backend + frontend test suites

test-backend: ## Backend unit + integration tests with coverage (needs Docker)
	cd backend && uv run pytest --cov --cov-report=term-missing:skip-covered

test-frontend: ## Frontend unit/component tests
	cd frontend && npm test

test-e2e: ## Full-stack Playwright tests (needs `make up migrate` and a build)
	cd frontend && npm run build && npx playwright test

lint: ## Lint backend + frontend
	cd backend && uv run ruff check . && uv run ruff format --check .
	cd frontend && npm run lint

typecheck: ## Type-check backend + frontend
	cd backend && uv run mypy app alembic/env.py
	cd frontend && npm run typecheck

format: ## Auto-format backend code
	cd backend && uv run ruff check --fix . && uv run ruff format .

check: lint typecheck test ## Everything CI runs (except E2E)
