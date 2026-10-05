"use client";

import { keepPreviousData, useQuery } from "@tanstack/react-query";

import { api } from "@/lib/api";
import type { PageParams } from "@/types/api";
import type { MisconceptionStatus, UsagePeriod } from "@/types/dashboard";

export const dashboardKey = ["dashboard"] as const;

export function useMasteryOverview(days = 30) {
  return useQuery({ queryKey: [...dashboardKey, "overview", days], queryFn: () => api.getMasteryOverview(days) });
}

export function useMasteryHeatmap(params: { subject_id?: string; weeks?: number; limit?: number } = {}) {
  return useQuery({
    queryKey: [...dashboardKey, "heatmap", params],
    queryFn: () => api.getMasteryHeatmap(params),
    placeholderData: keepPreviousData,
  });
}

export function useMisconceptions(params: PageParams & { status?: MisconceptionStatus; concept_id?: string } = {}) {
  return useQuery({
    queryKey: [...dashboardKey, "misconceptions", params],
    queryFn: () => api.listMisconceptions(params),
    placeholderData: keepPreviousData,
  });
}

export function useAIUsage(period: UsagePeriod) {
  return useQuery({
    queryKey: [...dashboardKey, "ai-usage", period],
    queryFn: () => api.getAIUsage(period),
    placeholderData: keepPreviousData,
  });
}
