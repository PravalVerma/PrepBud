"use client";

import { keepPreviousData, useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import { api } from "@/lib/api";
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
