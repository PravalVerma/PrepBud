import type { Metadata } from "next";

import { LoginForm } from "@/components/auth/login-form";

export const metadata: Metadata = { title: "Sign in" };

const ERRORS: Record<string, string> = {
  confirmation_failed: "That confirmation link is invalid or has expired. Please sign in or sign up again.",
};

export default async function LoginPage({
  searchParams,
}: {
  searchParams: Promise<{ next?: string; error?: string }>;
}) {
  const { next, error } = await searchParams;
  return (
    <main className="grid min-h-dvh place-items-center bg-gradient-to-b from-brand-50 to-slate-50 px-4">
      <div className="w-full max-w-md">
        <div className="mb-8 text-center">
          <span className="mx-auto mb-4 grid size-12 place-items-center rounded-xl bg-brand-600 text-lg font-bold text-white">
            SB
          </span>
          <h1 className="text-2xl font-semibold text-slate-900">School in a Box</h1>
          <p className="mt-1 text-sm text-slate-600">Your personal AI tutor for your own study material.</p>
        </div>
        <LoginForm next={next} initialError={error ? ERRORS[error] : undefined} />
      </div>
    </main>
  );
}
