"use client";

import { useQueryClient } from "@tanstack/react-query";
import { useTransition } from "react";

import { useProfile } from "@/hooks/use-profile";
import { signOut } from "@/lib/auth";

/** Current user identity (from the backend profile) and a sign-out action. */
export function useAuth() {
  const profile = useProfile();
  const queryClient = useQueryClient();
  const [isSigningOut, startTransition] = useTransition();

  return {
    email: profile.data?.email ?? null,
    displayName: profile.data?.display_name ?? null,
    isLoading: profile.isLoading,
    isSigningOut,
    signOut: () =>
      startTransition(async () => {
        queryClient.clear();
        await signOut();
      }),
  };
}
