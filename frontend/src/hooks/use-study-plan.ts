"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import { api } from "@/lib/api";
import type { PageParams } from "@/types/api";
import type { GoalCreate, GoalStatus, GoalUpdate, ReviewItemUpdate } from "@/types/study";

export const goalsKey = ["goals"] as const;
export const studyPlanKey = ["study-plan"] as const;

export function useGoals(params: PageParams & { status?: GoalStatus } = {}) {
  return useQuery({ queryKey: [...goalsKey, "list", params], queryFn: () => api.listGoals(params) });
}

export function useGoal(id: string | undefined) {
  return useQuery({
    queryKey: [...goalsKey, "detail", id],
    queryFn: () => api.getGoal(id!),
    enabled: Boolean(id),
  });
}

/** Goal changes regenerate the plan server-side, so both caches refresh. */
function useGoalMutation<V>(fn: (vars: V) => Promise<unknown>, meta: { success?: string; errorToast?: boolean }) {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: fn,
    meta,
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: goalsKey });
      void queryClient.invalidateQueries({ queryKey: studyPlanKey });
    },
  });
}

export const useCreateGoal = () =>
  useGoalMutation((body: GoalCreate) => api.createGoal(body), { success: "Goal created — your study plan is ready" });
export const useUpdateGoal = () =>
  useGoalMutation(({ id, body }: { id: string; body: GoalUpdate }) => api.updateGoal(id, body), {
    success: "Goal updated",
  });
export const useDeleteGoal = () =>
  useGoalMutation((id: string) => api.deleteGoal(id), { success: "Goal deleted", errorToast: true });

export function useStudyPlan() {
  return useQuery({ queryKey: studyPlanKey, queryFn: api.getStudyPlan });
}

export function useRegeneratePlan() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: api.regenerateStudyPlan,
    meta: { success: "Study plan rebuilt", errorToast: true },
    onSuccess: (plan) => queryClient.setQueryData(studyPlanKey, plan),
  });
}

export function useUpdateReviewItem() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: ({ id, body }: { id: string; body: ReviewItemUpdate }) => api.updateReviewItem(id, body),
    meta: { errorToast: true },
    onSuccess: () => queryClient.invalidateQueries({ queryKey: studyPlanKey }),
  });
}
