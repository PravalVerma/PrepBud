"use client";

import { keepPreviousData, useQuery } from "@tanstack/react-query";

import { api } from "@/lib/api";
import type { PageParams } from "@/types/api";
import type { ConceptFilters, SearchMode } from "@/types/domain";

export const conceptsKey = ["concepts"] as const;

export function useConcepts(params: PageParams & ConceptFilters) {
  return useQuery({
    queryKey: [...conceptsKey, "list", params],
    queryFn: () => api.listConcepts(params),
    placeholderData: keepPreviousData,
  });
}

export function useConcept(id: string) {
  return useQuery({ queryKey: [...conceptsKey, "detail", id], queryFn: () => api.getConcept(id) });
}

export function useConceptGraph(id: string, depth = 1) {
  return useQuery({
    queryKey: [...conceptsKey, "graph", id, depth],
    queryFn: () => api.getConceptGraph(id, depth),
  });
}

export function useSearch(
  q: string,
  options: { mode?: SearchMode; concept_id?: string; per_page?: number } = {},
) {
  const text = q.trim();
  return useQuery({
    queryKey: ["search", text, options],
    queryFn: () => api.search({ q: text, ...options }),
    enabled: text.length > 0,
  });
}
