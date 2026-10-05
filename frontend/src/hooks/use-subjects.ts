"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import { api } from "@/lib/api";
import type { PageParams } from "@/types/api";
import type { SubjectCreate } from "@/types/domain";

export const subjectsKey = ["subjects"] as const;

export function useSubjects(params: PageParams = { page: 1, per_page: 20 }) {
  return useQuery({
    queryKey: [...subjectsKey, params],
    queryFn: () => api.listSubjects(params),
  });
}

export function useCreateSubject() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (body: SubjectCreate) => api.createSubject(body),
    meta: { success: "Subject added" },
    onSuccess: () => queryClient.invalidateQueries({ queryKey: subjectsKey }),
  });
}
