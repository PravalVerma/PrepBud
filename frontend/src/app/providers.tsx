"use client";

import { MutationCache, QueryCache, QueryClient, QueryClientProvider } from "@tanstack/react-query";
import type { ReactNode } from "react";

import { ApiError } from "@/lib/api";
import { LOGIN_PATH } from "@/lib/routes";

function redirectIfSignedOut(error: unknown): void {
  if (error instanceof ApiError && error.status === 401 && typeof window !== "undefined") {
    const next = encodeURIComponent(window.location.pathname + window.location.search);
    // Deliberately a full page load: the session expired, so all client state
    // (query cache, stores) must be discarded. Runs outside React, so no router.
    // eslint-disable-next-line @next/next/no-location-assign-relative-destination
    window.location.assign(`${LOGIN_PATH}?next=${next}`);
  }
}

function makeQueryClient(): QueryClient {
  return new QueryClient({
    queryCache: new QueryCache({ onError: redirectIfSignedOut }),
    mutationCache: new MutationCache({ onError: redirectIfSignedOut }),
    defaultOptions: {
      queries: {
        staleTime: 30_000,
        retry: (count, error) =>
          !(error instanceof ApiError && error.status >= 400 && error.status < 500) && count < 2,
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
  return <QueryClientProvider client={getQueryClient()}>{children}</QueryClientProvider>;
}
