/**
 * Server-side environment, read at request time (not inlined at build time), so
 * one build runs in every environment. All Supabase + API traffic is server-side
 * (proxy, server actions, the /api/backend route handler); nothing here reaches
 * the browser.
 */
import "server-only";

function read(name: string): string {
  const value = process.env[name];
  if (value === undefined || value === "") {
    throw new Error(`Missing required environment variable ${name}`);
  }
  return value;
}

export const env = {
  get supabaseUrl(): string {
    return read("SUPABASE_URL");
  },
  get supabaseAnonKey(): string {
    return read("SUPABASE_ANON_KEY");
  },
  /** FastAPI base URL including the version prefix, e.g. http://localhost:8000/api/v1 */
  get apiUrl(): string {
    return read("API_URL").replace(/\/+$/, "");
  },
  /** Public origin used in e-mail links; falls back to the request origin when unset. */
  get siteUrl(): string | undefined {
    return process.env.SITE_URL?.replace(/\/+$/, "") || undefined;
  },
  get isProduction(): boolean {
    return process.env.NODE_ENV === "production";
  },
};
