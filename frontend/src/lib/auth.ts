"use server";

/**
 * Supabase auth server actions. Credentials go browser → Next server → Supabase;
 * the resulting session is stored only in httpOnly cookies.
 */
import { headers } from "next/headers";
import { redirect } from "next/navigation";

import { env } from "@/lib/env";
import { HOME_PATH, LOGIN_PATH, safeNextPath } from "@/lib/routes";
import { createSupabaseServerClient } from "@/lib/supabase";

export type AuthFormState = {
  error?: string;
  message?: string;
};

const MIN_PASSWORD_LENGTH = 8;

function readCredentials(formData: FormData): { email: string; password: string } | string {
  const email = String(formData.get("email") ?? "").trim();
  const password = String(formData.get("password") ?? "");
  if (!/^[^\s@]+@[^\s@]+\.[^\s@]+$/.test(email)) return "Enter a valid email address.";
  if (password.length < MIN_PASSWORD_LENGTH) {
    return `Password must be at least ${MIN_PASSWORD_LENGTH} characters.`;
  }
  return { email, password };
}

/**
 * Sync the Supabase identity into PostgreSQL (POST /auth/callback). The backend
 * also provisions on any first authenticated call, so a failure here is not fatal.
 */
async function syncUser(accessToken: string, displayName?: string): Promise<void> {
  try {
    await fetch(`${env.apiUrl}/auth/callback`, {
      method: "POST",
      headers: { Authorization: `Bearer ${accessToken}`, "Content-Type": "application/json" },
      body: JSON.stringify(displayName ? { display_name: displayName } : {}),
      cache: "no-store",
      signal: AbortSignal.timeout(5_000),
    });
  } catch {
    // Provisioning will happen on the first API call instead.
  }
}

export async function signIn(_prev: AuthFormState, formData: FormData): Promise<AuthFormState> {
  const creds = readCredentials(formData);
  if (typeof creds === "string") return { error: creds };

  const supabase = await createSupabaseServerClient();
  const { data, error } = await supabase.auth.signInWithPassword(creds);
  if (error || !data.session) {
    return { error: "Incorrect email or password." };
  }
  await syncUser(data.session.access_token);
  redirect(safeNextPath(String(formData.get("next") ?? "")));
}

export async function signUp(_prev: AuthFormState, formData: FormData): Promise<AuthFormState> {
  const creds = readCredentials(formData);
  if (typeof creds === "string") return { error: creds };
  const displayName = String(formData.get("display_name") ?? "").trim() || undefined;

  // Prefer the configured origin; Supabase additionally checks redirect URLs
  // against the project's allow-list.
  const h = await headers();
  const origin =
    env.siteUrl ?? h.get("origin") ?? `${h.get("x-forwarded-proto") ?? "http"}://${h.get("host")}`;

  const supabase = await createSupabaseServerClient();
  const { data, error } = await supabase.auth.signUp({
    ...creds,
    options: {
      emailRedirectTo: `${origin}/auth/confirm`,
      data: displayName ? { full_name: displayName } : undefined,
    },
  });
  if (error) return { error: error.message };

  if (data.session) {
    // E-mail confirmation disabled: signed in immediately.
    await syncUser(data.session.access_token, displayName);
    redirect(HOME_PATH);
  }
  return { message: "Check your email for a confirmation link to finish signing up." };
}

export async function signOut(): Promise<void> {
  const supabase = await createSupabaseServerClient();
  await supabase.auth.signOut();
  redirect(LOGIN_PATH);
}
