"use client";

import { MutationCache, QueryCache, QueryClient, QueryClientProvider } from "@tanstack/react-query";
import type { ReactNode } from "react";

import { Toaster } from "@/components/ui/toaster";
import { ApiError } from "@/lib/api";
import { errorMessage, shouldRetry } from "@/lib/errors";
import { LOGIN_PATH } from "@/lib/routes";
import { toast } from "@/stores/toast-store";

/**
 * Per-mutation UX, declared next to the mutation:
 *   meta: { success: "Goal created" }   → success toast
 *   meta: { errorToast: true }          → error toast (for actions without an inline error)
 */
declare module "@tanstack/react-query" {
  interface Register {
    mutationMeta: { success?: string; errorToast?: boolean };
  }
}

function redirectIfSignedOut(error: unknown): boolean {
  if (error instanceof ApiError && error.status === 401 && typeof window !== "undefined") {
    const next = encodeURIComponent(window.location.pathname + window.location.search);
    // Deliberately a full page load: the session expired, so all client state
    // (query cache, stores) must be discarded. Runs outside React, so no router.
    // eslint-disable-next-line @next/next/no-location-assign-relative-destination
    window.location.assign(`${LOGIN_PATH}?next=${next}`);
    return true;
  }
  return false;
}

function makeQueryClient(): QueryClient {
  return new QueryClient({
    queryCache: new QueryCache({ onError: (error) => void redirectIfSignedOut(error) }),
    mutationCache: new MutationCache({
      onSuccess: (_data, _vars, _ctx, mutation) => {
        if (mutation.meta?.success) toast.success(mutation.meta.success);
      },
      onError: (error, _vars, _ctx, mutation) => {
        if (redirectIfSignedOut(error)) return;
        if (mutation.meta?.errorToast) toast.error(errorMessage(error));
      },
    }),
    defaultOptions: {
      queries: {
        staleTime: 30_000,
        retry: shouldRetry,
        retryDelay: (attempt) => Math.min(1000 * 2 ** attempt, 8000),
      },
    },
  });
}

let browserQueryClient: QueryClient | undefined;

function getQueryClient(): QueryClient {
  // Isolate server renders; reuse one client in the browser.
  if (typeof window === "undefined") return makeQueryClient();
  browserQueryClient ??= makeQueryClient();
  return browserQueryClient;
}

export function Providers({ children }: { children: ReactNode }) {
  return (
    <QueryClientProvider client={getQueryClient()}>
      {children}
      <Toaster />
    </QueryClientProvider>
  );
}
