"use client";

import { useActionState, useState } from "react";

import { Button } from "@/components/ui/button";
import { Card } from "@/components/ui/card";
import { TextField } from "@/components/ui/field";
import { Alert } from "@/components/ui/feedback";
import { type AuthFormState, signIn, signUp } from "@/lib/auth";
import { cn } from "@/lib/utils";

type Mode = "sign-in" | "sign-up";

export function LoginForm({ next, initialError }: { next?: string; initialError?: string }) {
  const [mode, setMode] = useState<Mode>("sign-in");
  const [signInState, signInAction, signingIn] = useActionState<AuthFormState, FormData>(signIn, {});
  const [signUpState, signUpAction, signingUp] = useActionState<AuthFormState, FormData>(signUp, {});

  const isSignIn = mode === "sign-in";
  const state = isSignIn ? signInState : signUpState;
  const pending = isSignIn ? signingIn : signingUp;
  const error = state.error ?? (isSignIn ? initialError : undefined);

  return (
    <Card className="p-6">
      <div role="tablist" aria-label="Authentication" className="mb-6 grid grid-cols-2 rounded-lg bg-slate-100 p-1">
        {(["sign-in", "sign-up"] as const).map((m) => (
          <button
            key={m}
            type="button"
            role="tab"
            aria-selected={mode === m}
            onClick={() => setMode(m)}
            className={cn(
              "rounded-md py-1.5 text-sm font-medium transition-colors",
              mode === m ? "bg-white text-slate-900 shadow-sm" : "text-slate-600 hover:text-slate-900",
            )}
          >
            {m === "sign-in" ? "Sign in" : "Create account"}
          </button>
        ))}
      </div>

      <form action={isSignIn ? signInAction : signUpAction} className="space-y-4" noValidate>
        {next && <input type="hidden" name="next" value={next} />}
        {!isSignIn && (
          <TextField label="Your name" name="display_name" autoComplete="name" maxLength={100} />
        )}
        <TextField label="Email" name="email" type="email" autoComplete="email" required />
        <TextField
          label="Password"
          name="password"
          type="password"
          autoComplete={isSignIn ? "current-password" : "new-password"}
          minLength={8}
          hint={isSignIn ? undefined : "At least 8 characters."}
          required
        />

        {error && <Alert tone="error">{error}</Alert>}
        {state.message && <Alert tone="success">{state.message}</Alert>}

        <Button type="submit" className="w-full" loading={pending}>
          {isSignIn ? "Sign in" : "Create account"}
        </Button>
      </form>
    </Card>
  );
}
