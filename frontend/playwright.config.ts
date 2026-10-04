import { defineConfig, devices } from "@playwright/test";

/**
 * E2E runs the real stack: Next.js (production build) → FastAPI → PostgreSQL/Redis,
 * with a local mock of Supabase Auth (tests/e2e/support/mock-supabase-auth.mjs)
 * issuing ES256 JWTs via JWKS.
 *
 * Prerequisites: `docker compose up -d`, backend migrated (`alembic upgrade head`),
 * and `npm run build`. Set E2E_BASE_URL to test an already-running deployment instead.
 */
const WEB_PORT = Number(process.env.E2E_PORT ?? 3100);
const API_PORT = Number(process.env.E2E_API_PORT ?? 8001);
const AUTH_PORT = Number(process.env.E2E_AUTH_PORT ?? 54329);
const baseURL = process.env.E2E_BASE_URL ?? `http://localhost:${WEB_PORT}`;
const authUrl = `http://127.0.0.1:${AUTH_PORT}`;
const apiUrl = `http://127.0.0.1:${API_PORT}`;
const reuse = !process.env.CI;

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
          command: `uv run uvicorn app.main:app --host 127.0.0.1 --port ${API_PORT}`,
          cwd: "../backend",
          url: `${apiUrl}/health`,
          env: {
            SUPABASE_URL: authUrl,
            SUPABASE_JWT_SECRET: "",
            SUPABASE_JWKS_URL: "",
            FRONTEND_URL: baseURL,
            RATE_LIMIT_PER_MINUTE: "1000",
            LOG_LEVEL: "WARNING",
          },
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
