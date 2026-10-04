/**
 * Supabase client initialisation (server-side only).
 *
 * The browser never holds Supabase tokens: sign-in happens in server actions and
 * the session lives in httpOnly cookies, refreshed by `src/proxy.ts`.
 */
import "server-only";

import { createServerClient } from "@supabase/ssr";
import type { SupabaseClient } from "@supabase/supabase-js";
import { cookies } from "next/headers";
import { type NextRequest, NextResponse } from "next/server";

import { hardenAuthCookie } from "@/lib/cookies";
import { env } from "@/lib/env";

/** For Server Components, Server Actions and Route Handlers. */
export async function createSupabaseServerClient(): Promise<SupabaseClient> {
  const cookieStore = await cookies();
  return createServerClient(env.supabaseUrl, env.supabaseAnonKey, {
    cookies: {
      getAll: () => cookieStore.getAll(),
      setAll: (toSet) => {
        try {
          for (const { name, value, options } of toSet) {
            cookieStore.set(name, value, hardenAuthCookie(name, options, env.isProduction));
          }
        } catch {
          // Server Components cannot write cookies; the proxy refreshes the
          // session on the next request instead.
        }
      },
    },
  });
}

/**
 * For `src/proxy.ts`: reads cookies from the request and writes refreshed ones to
 * both the forwarded request and the response (plus the no-cache headers the
 * library requires whenever auth cookies are set).
 */
export function createSupabaseProxyClient(request: NextRequest): {
  supabase: SupabaseClient;
  /** The pass-through response, carrying any refreshed cookies. Read after auth calls. */
  response: () => NextResponse;
} {
  let response = NextResponse.next({ request });
  const supabase = createServerClient(env.supabaseUrl, env.supabaseAnonKey, {
    cookies: {
      getAll: () => request.cookies.getAll(),
      setAll: (toSet, headers) => {
        for (const { name, value } of toSet) request.cookies.set(name, value);
        response = NextResponse.next({ request });
        for (const { name, value, options } of toSet) {
          response.cookies.set(name, value, hardenAuthCookie(name, options, env.isProduction));
        }
        for (const [key, value] of Object.entries(headers ?? {})) {
          response.headers.set(key, value);
        }
      },
    },
  });
  return { supabase, response: () => response };
}
