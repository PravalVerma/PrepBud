/**
 * E-mail confirmation landing (sign-up / magic links).
 *
 * Supports both the PKCE `?code=` flow and the `?token_hash=&type=` OTP flow.
 * Responds with a tiny HTML page that navigates onward itself: a same-origin
 * navigation is needed for the browser to send the new `SameSite=Strict`
 * session cookies, which a cross-site redirect chain from the e-mail would not.
 */
import type { EmailOtpType } from "@supabase/supabase-js";
import type { NextRequest } from "next/server";

import { HOME_PATH, LOGIN_PATH, safeNextPath } from "@/lib/routes";
import { createSupabaseServerClient } from "@/lib/supabase";

export const dynamic = "force-dynamic";

function navigate(to: string): Response {
  const href = to.replace(/"/g, "%22").replace(/</g, "%3C");
  const html = `<!doctype html><meta charset="utf-8"><meta http-equiv="refresh" content="0;url=${href}"><title>Signing in…</title><a href="${href}">Continue</a>`;
  return new Response(html, {
    status: 200,
    headers: { "Content-Type": "text/html; charset=utf-8", "Cache-Control": "no-store" },
  });
}

export async function GET(request: NextRequest): Promise<Response> {
  const params = request.nextUrl.searchParams;
  const next = safeNextPath(params.get("next") ?? HOME_PATH);
  const supabase = await createSupabaseServerClient();

  const code = params.get("code");
  const tokenHash = params.get("token_hash");
  const type = params.get("type") as EmailOtpType | null;

  let ok = false;
  if (code) {
    ok = !(await supabase.auth.exchangeCodeForSession(code)).error;
  } else if (tokenHash && type) {
    ok = !(await supabase.auth.verifyOtp({ token_hash: tokenHash, type })).error;
  }
  return navigate(ok ? next : `${LOGIN_PATH}?error=confirmation_failed`);
}
