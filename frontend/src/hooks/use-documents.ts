"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import { api } from "@/lib/api";
import type { PageParams } from "@/types/api";
import type { DocumentSummary } from "@/types/domain";

export const documentsKey = ["documents"] as const;
const POLL_MS = 2_000;

const isActive = (d: Pick<DocumentSummary, "processing_status">) => d.processing_status === "processing";

/** The user's documents; polls while any of them is still being processed (AC-3.1). */
export function useDocuments(params: PageParams = { page: 1, per_page: 20 }) {
  return useQuery({
    queryKey: [...documentsKey, "list", params],
    queryFn: () => api.listDocuments(params),
    refetchInterval: (q) => (q.state.data?.data.some(isActive) ? POLL_MS : false),
  });
}

export function useDocument(id: string) {
  return useQuery({
    queryKey: [...documentsKey, "detail", id],
    queryFn: () => api.getDocument(id),
    refetchInterval: (q) => (q.state.data && isActive(q.state.data) ? POLL_MS : false),
  });
}

function useInvalidating<T>(fn: (id: string) => Promise<T>) {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: fn,
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: documentsKey });
      void queryClient.invalidateQueries({ queryKey: ["concepts"] });
    },
  });
}

export function useDeleteDocument() {
  return useInvalidating((id) => api.deleteDocument(id));
}

/** Re-queue a failed document (confirm-upload again). */
export function useRetryDocument() {
  return useInvalidating((id) => api.confirmUpload(id));
}
