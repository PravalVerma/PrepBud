# School in a Box — Frontend

Next.js 16 (App Router, TypeScript, Tailwind v4), TanStack Query for server
state, Zustand for client UI state, Supabase Auth via `@supabase/ssr`.

> Next.js 16 renamed `middleware.ts` to **`src/proxy.ts`** and made `cookies()`,
> `params` and `searchParams` async-only. Version-matched docs ship in
> `node_modules/next/dist/docs/`.

```bash
npm install
npm run dev          # http://localhost:3000
npm run lint && npm run typecheck && npm test
npm run build && npx playwright test   # E2E: needs PostgreSQL/Redis up + migrated
```

Environment (`frontend/.env.local`, read at request time on the server):
`SUPABASE_URL`, `SUPABASE_ANON_KEY`, `API_URL` (e.g. `http://localhost:8000/api/v1`),
`SITE_URL`.

## Structure

```text
src/
  proxy.ts                    session refresh + route protection
  app/                        login, dashboard, profile, auth/confirm,
                              api/backend/[...path] (authenticated API proxy)
  components/{ui,layout,auth,dashboard,profile}
  lib/   api.ts (browser client) · auth.ts (server actions) · supabase.ts ·
         backend.ts (BFF forwarding) · routes.ts · cookies.ts · env.ts · session.ts
  hooks/ use-auth · use-profile · use-subjects
  stores/ ui-store (Zustand)
  types/ api.ts (envelope) · domain.ts
tests/
  lib/, components/           vitest + Testing Library
  e2e/                        Playwright (+ support/mock-supabase-auth.mjs)
```

Supabase tokens never reach page scripts: auth cookies are `httpOnly` and
`SameSite=Strict`, and API calls go through `/api/backend/*`, which adds the
bearer token on the server.
