"use client";

import { keepPreviousData, useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useRouter } from "next/navigation";

import { ApiError, api } from "@/lib/api";
import type { PageParams } from "@/types/api";
import type { SessionCreate, SessionStatus } from "@/types/session";

export const sessionsKey = ["sessions"] as const;
export const sessionListKey = [...sessionsKey, "list"] as const;

export function useSessions(params: PageParams & { status?: SessionStatus } = {}) {
  return useQuery({
    queryKey: [...sessionListKey, params],
    queryFn: () => api.listSessions(params),
    placeholderData: keepPreviousData,
  });
}

export function useCreateSession() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (body: SessionCreate) => api.createSession(body),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: sessionListKey }),
  });
}

/** Create a session and open it. */
export function useStartSession() {
  const router = useRouter();
  const create = useCreateSession();
  return {
    ...create,
    start: (body: SessionCreate) =>
      create.mutate(body, { onSuccess: (created) => router.push(`/session/${created.session_id}`) }),
  };
}

/** A user-facing reason a session could not start. */
export function startErrorMessage(error: unknown): string {
  if (error instanceof ApiError) {
    if (error.status === 422 && error.details.reason === "NO_CONCEPTS") {
      return error.message || "There is nothing to study yet.";
    }
    if (error.status === 429) return "You've started a lot of sessions recently — try again a little later.";
    return error.message;
  }
  return "Could not start the session.";
}
