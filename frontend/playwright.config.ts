import { defineConfig, devices } from "@playwright/test";

/**
 * E2E runs the real stack: Next.js (production build) → FastAPI → PostgreSQL/Redis,
 * plus the content pipeline (Celery worker → S3 → Qdrant). Two local mocks stand in for
 * external services:
 *   - Supabase Auth (tests/e2e/support/mock-supabase-auth.mjs) issuing ES256 JWTs via JWKS,
 *   - an OpenAI-compatible LLM (tests/e2e/support/mock-llm.mjs), reached through the
 *     backend's normal provider path (LLM_BASE_URL).
 *
 * Prerequisites: `docker compose up -d --wait` (PostgreSQL, Redis, Qdrant, S3), backend
 * migrated (`alembic upgrade head`), and `npm run build`. The worker consumes its own
 * Celery queue ("e2e") so a developer's worker on the same Redis never picks up E2E jobs.
 * Set E2E_BASE_URL to test an already-running deployment instead.
 */
const WEB_PORT = Number(process.env.E2E_PORT ?? 3100);
const API_PORT = Number(process.env.E2E_API_PORT ?? 8002);
const AUTH_PORT = Number(process.env.E2E_AUTH_PORT ?? 54329);
const LLM_PORT = Number(process.env.E2E_LLM_PORT ?? 54330);
const baseURL = process.env.E2E_BASE_URL ?? `http://localhost:${WEB_PORT}`;
const authUrl = `http://127.0.0.1:${AUTH_PORT}`;
const apiUrl = `http://127.0.0.1:${API_PORT}`;
const reuse = !process.env.CI;

/** Shared by the API and the worker. Infra URLs default to docker-compose.yml. */
const backendEnv = {
  SUPABASE_URL: authUrl,
  SUPABASE_JWT_SECRET: "",
  SUPABASE_JWKS_URL: "",
  FRONTEND_URL: baseURL,
  RATE_LIMIT_PER_MINUTE: "1000",
  RATE_LIMIT_UPLOADS_PER_HOUR: "1000",
  RATE_LIMIT_SESSIONS_PER_HOUR: "1000",
  LOG_LEVEL: "INFO",
  CELERY_QUEUE: "e2e",
  // Hermetic: override every AI setting a developer's .env might set (provider, models, size).
  LLM_DEFAULT_PROVIDER: "openai",
  LLM_BASE_URL: `http://127.0.0.1:${LLM_PORT}/v1`,
  LLM_API_KEY: "e2e-key",
  ...Object.fromEntries(
    [
      "TUTOR_EXPLANATION",
      "QUESTION_GENERATION",
      "ANSWER_EVALUATION",
      "MISCONCEPTION_DETECTION",
      "CONCEPT_EXTRACTION",
      "SESSION_SUMMARY",
      "EMBEDDING",
    ].flatMap((task) => [
      [`LLM_TASKS__${task}__PROVIDER`, "openai"],
      [`LLM_TASKS__${task}__MODEL`, `mock-${task.toLowerCase()}`],
    ]),
  ),
  LLM_TASKS__EMBEDDING__DIMENSIONS: "1536",
  CONTENT__EXTRACTION_CONCURRENCY: "6",
  CONTENT__EMBEDDING_BATCH_SIZE: "64",
  AI_PRICING: "{}",
  QDRANT_URL: process.env.QDRANT_URL ?? "http://localhost:6333",
  QDRANT_SECTIONS_COLLECTION: "e2e_document_sections",
  S3_ENDPOINT_URL: process.env.S3_ENDPOINT_URL ?? "http://localhost:8333",
  S3_BUCKET: process.env.S3_BUCKET ?? "siab-uploads",
  S3_ACCESS_KEY_ID: process.env.S3_ACCESS_KEY_ID ?? "siab-local-access",
  S3_SECRET_ACCESS_KEY: process.env.S3_SECRET_ACCESS_KEY ?? "siab-local-secret-key",
};

export default defineConfig({
  testDir: "./tests/e2e",
  fullyParallel: false,
  forbidOnly: !!process.env.CI,
  retries: process.env.CI ? 1 : 0,
  reporter: process.env.CI ? [["github"], ["list"]] : "list",
  use: { baseURL, trace: "retain-on-failure" },
  projects: [{ name: "chromium", use: { ...devices["Desktop Chrome"] } }],
  webServer: process.env.E2E_BASE_URL
    ? undefined
    : [
        {
          command: "node tests/e2e/support/mock-supabase-auth.mjs",
          url: `${authUrl}/health`,
          env: { PORT: String(AUTH_PORT) },
          reuseExistingServer: reuse,
        },
        {
          command: "node tests/e2e/support/mock-llm.mjs",
          url: `http://127.0.0.1:${LLM_PORT}/health`,
          env: { PORT: String(LLM_PORT) },
          reuseExistingServer: reuse,
        },
        {
          command: `uv run uvicorn app.main:app --host 127.0.0.1 --port ${API_PORT}`,
          cwd: "../backend",
          url: `${apiUrl}/health`,
          env: { ...backendEnv, LOG_LEVEL: "WARNING" },
          reuseExistingServer: reuse,
          timeout: 120_000,
        },
        {
          // Celery's prefork pool is unavailable on Windows; solo works everywhere.
          command: "uv run celery -A app.workers.celery_app worker --pool=solo -l info",
          cwd: "../backend",
          env: backendEnv,
          wait: { stdout: /ready\./ },
          reuseExistingServer: reuse,
          timeout: 120_000,
        },
        {
          command: `npm run start -- -p ${WEB_PORT}`,
          url: `${baseURL}/login`,
          env: {
            SUPABASE_URL: authUrl,
            SUPABASE_ANON_KEY: "e2e-anon-key",
            API_URL: `${apiUrl}/api/v1`,
            SITE_URL: baseURL,
          },
          reuseExistingServer: reuse,
          timeout: 120_000,
        },
      ],
});
