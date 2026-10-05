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

/** `https://api.example.com/api/v1` → `wss://api.example.com` */
export function toWsOrigin(url: string): string {
  const { protocol, host } = new URL(url);
  return `${protocol === "https:" || protocol === "wss:" ? "wss" : "ws"}://${host}`;
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
  /**
   * ws(s):// origin the *browser* uses for session WebSockets (the BFF cannot proxy them).
   * `PUBLIC_API_URL` when the API's public address differs from `API_URL` (e.g. API_URL is
   * an internal hostname); otherwise derived from `API_URL`.
   */
  get wsUrl(): string {
    return toWsOrigin(process.env.PUBLIC_API_URL || read("API_URL"));
  },
  /** Public origin used in e-mail links; falls back to the request origin when unset. */
  get siteUrl(): string | undefined {
    return process.env.SITE_URL?.replace(/\/+$/, "") || undefined;
  },
  get isProduction(): boolean {
    return process.env.NODE_ENV === "production";
  },
};
