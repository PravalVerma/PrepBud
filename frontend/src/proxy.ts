/**
 * Next.js Proxy (formerly middleware): refreshes the Supabase session on every
 * page request and enforces sign-in for protected routes.
 *
 * `/api/*` is excluded — the backend route handler authenticates itself and
 * answers 401 instead of redirecting.
 */
import { type NextRequest, NextResponse } from "next/server";

import { decideRoute } from "@/lib/routes";
import { createSupabaseProxyClient } from "@/lib/supabase";

export async function proxy(request: NextRequest): Promise<NextResponse> {
  let response = () => NextResponse.next({ request });
  let isAuthenticated = false;
  try {
    const client = createSupabaseProxyClient(request);
    response = client.response;
    // Validates the JWT (and refreshes it when expired) — must run before any
    // response is produced so refreshed cookies are written back.
    const { data, error } = await client.supabase.auth.getClaims();
    isAuthenticated = !error && !!data?.claims?.sub;
  } catch {
    // Misconfiguration or Supabase outage: fail closed (treated as signed out).
    isAuthenticated = false;
  }

  const { pathname, search } = request.nextUrl;
  const decision = decideRoute(pathname, search, isAuthenticated);
  if (decision.type === "redirect") {
    const redirect = NextResponse.redirect(new URL(decision.to, request.url));
    for (const cookie of response().cookies.getAll()) redirect.cookies.set(cookie);
    redirect.headers.set("Cache-Control", "private, no-store");
    return redirect;
  }
  return response();
}

export const config = {
  matcher: [
    "/((?!api/|_next/static|_next/image|favicon.ico|robots.txt|sitemap.xml|.*\\.(?:svg|png|jpg|jpeg|gif|webp|ico)$).*)",
  ],
};
