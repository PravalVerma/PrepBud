/** Server-side session accessors. Never import from client components. */
import "server-only";

import { createSupabaseServerClient } from "@/lib/supabase";

/**
 * The current Supabase access token (refreshed if expired), or null when signed
 * out. The FastAPI backend re-verifies the JWT on every call, so reading it from
 * the session cookie here is safe.
 */
export async function getAccessToken(): Promise<string | null> {
  try {
    const supabase = await createSupabaseServerClient();
    const { data } = await supabase.auth.getSession();
    return data.session?.access_token ?? null;
  } catch {
    return null;
  }
}
