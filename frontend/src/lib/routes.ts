/** Route-protection rules applied by `src/proxy.ts` (pure, unit-tested). */

export const LOGIN_PATH = "/login";
export const HOME_PATH = "/dashboard";

/** Paths reachable without a session. Everything else requires sign-in. */
const PUBLIC_PREFIXES = [LOGIN_PATH, "/auth/"];

export type RouteDecision = { type: "allow" } | { type: "redirect"; to: string };

export function isPublicPath(pathname: string): boolean {
  return PUBLIC_PREFIXES.some((p) => pathname === p || pathname.startsWith(p.endsWith("/") ? p : `${p}/`));
}

/**
 * Only same-origin, absolute-path redirects are honoured (no `//evil.com`,
 * no `https://...`), preventing open redirects via `?next=`.
 */
export function safeNextPath(next: string | null | undefined): string {
  if (!next || !next.startsWith("/") || next.startsWith("//") || next.startsWith("/\\")) {
    return HOME_PATH;
  }
  return next;
}

export function decideRoute(
  pathname: string,
  search: string,
  isAuthenticated: boolean,
): RouteDecision {
  if (!isAuthenticated && !isPublicPath(pathname)) {
    const next = pathname === "/" ? "" : `?next=${encodeURIComponent(pathname + search)}`;
    return { type: "redirect", to: `${LOGIN_PATH}${next}` };
  }
  if (isAuthenticated && pathname === LOGIN_PATH) {
    return { type: "redirect", to: HOME_PATH };
  }
  return { type: "allow" };
}
