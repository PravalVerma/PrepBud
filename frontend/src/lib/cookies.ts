import type { CookieOptions } from "@supabase/ssr";

/**
 * Hardened options for every Supabase auth cookie (SECURITY_MODEL §2.2):
 * `httpOnly` (never readable by page scripts), `secure` in production and
 * `sameSite=strict`.
 *
 * Exception: the short-lived PKCE code-verifier cookie must survive the
 * cross-site navigation from the confirmation e-mail back to /auth/confirm, so
 * it is `lax`. It holds no session credentials.
 */
export function hardenAuthCookie(
  name: string,
  options: CookieOptions,
  isProduction: boolean,
): CookieOptions {
  return {
    ...options,
    path: options.path ?? "/",
    httpOnly: true,
    secure: isProduction,
    sameSite: name.endsWith("-code-verifier") ? "lax" : "strict",
  };
}
