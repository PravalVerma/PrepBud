"use client";

import { useQuery } from "@tanstack/react-query";
import Link from "next/link";
import { useEffect, useRef } from "react";

import { Button } from "@/components/ui/button";
import { Card, CardHeader } from "@/components/ui/card";
import { Skeleton } from "@/components/ui/feedback";
import { useProfile, useUpdateProfile } from "@/hooks/use-profile";
import { useSessions } from "@/hooks/use-sessions";
import { useGoals } from "@/hooks/use-study-plan";
import { api } from "@/lib/api";
import { cn } from "@/lib/utils";
import type { OnboardingState } from "@/types/domain";

const ORDER: OnboardingState[] = ["new", "profile_set", "first_upload", "first_session", "complete"];

interface Step {
  key: string;
  title: string;
  description: string;
  href: string;
  cta: string;
  done: boolean;
  waiting?: string;
  optional?: boolean;
}

/** The furthest onboarding state the student's data supports. */
export function derivedState(s: { profile: boolean; upload: boolean; session: boolean }): OnboardingState {
  if (!s.profile) return "new";
  if (!s.upload) return "profile_set";
  if (!s.session) return "first_upload";
  return "first_session";
}

/**
 * Guided first use (AC-7.4): profile → upload → processing → first session → goal. Steps tick
 * themselves off from real data; `onboarding_state` advances as they do, and the card hides
 * once the student finishes (or dismisses) it.
 */
export function OnboardingChecklist() {
  const { data: profile, isLoading } = useProfile();
  const update = useUpdateProfile();
  const finished = profile?.onboarding_state === "complete";
  const docs = useQuery({
    queryKey: ["documents", "onboarding"],
    queryFn: async () => {
      const [all, ready] = await Promise.all([
        api.listDocuments({ per_page: 1 }),
        api.listDocuments({ per_page: 1, status: "ready" }),
      ]);
      return { total: all.meta.pagination.total, ready: ready.meta.pagination.total };
    },
    enabled: Boolean(profile) && !finished,
    refetchInterval: (q) => (q.state.data && q.state.data.total > q.state.data.ready ? 5000 : false),
  });
  const sessions = useSessions({ per_page: 10 });
  const goals = useGoals({ per_page: 1 });
  const advanced = useRef<OnboardingState | null>(null);

  const stored = profile?.onboarding_state ?? "new";
  const profileDone = stored !== "new";
  const uploaded = (docs.data?.total ?? 0) > 0;
  const ready = (docs.data?.ready ?? 0) > 0;
  const studied = (sessions.data?.data ?? []).some((s) => s.interaction_count > 0 || s.status === "completed");
  const hasGoal = (goals.data?.meta.pagination.total ?? 0) > 0;
  const loaded = Boolean(profile) && docs.isSuccess && sessions.isSuccess;
  const target = derivedState({ profile: profileDone, upload: uploaded, session: studied });

  useEffect(() => {
    if (!loaded || finished || update.isPending) return;
    if (ORDER.indexOf(target) > ORDER.indexOf(stored) && advanced.current !== target) {
      advanced.current = target;
      update.mutate({ onboarding_state: target });
    }
  }, [loaded, finished, target, stored, update]);

  if (isLoading || !profile || finished) return isLoading ? <Skeleton className="h-40 w-full rounded-xl" /> : null;

  const steps: Step[] = [
    {
      key: "profile",
      title: "Tell your tutor about yourself",
      description: "Grade level and difficulty help pitch explanations right.",
      href: "/profile",
      cta: "Set up profile",
      done: profileDone,
    },
    {
      key: "upload",
      title: "Upload your study material",
      description: "Notes, textbook chapters or worksheets — PDF, images or text.",
      href: "/upload",
      cta: "Upload",
      done: uploaded,
    },
    {
      key: "ready",
      title: "Let your tutor read it",
      description: "Concepts and their prerequisites are extracted automatically.",
      href: "/concepts",
      cta: "See concepts",
      done: ready,
      waiting: uploaded && !ready ? "Processing…" : undefined,
    },
    {
      key: "session",
      title: "Start your first study session",
      description: "A short explanation, then a few questions to find your level.",
      href: "/session",
      cta: "Start studying",
      done: studied,
    },
    {
      key: "goal",
      title: "Set a goal",
      description: "Get a day-by-day study plan with spaced reviews.",
      href: "/goals",
      cta: "Set a goal",
      done: hasGoal,
      optional: true,
    },
  ];
  const required = steps.filter((s) => !s.optional);
  const doneCount = required.filter((s) => s.done).length;
  const allDone = doneCount === required.length;
  const next = steps.find((s) => !s.done && !s.waiting);

  return (
    <Card data-testid="onboarding" className="space-y-4">
      <CardHeader
        title={allDone ? "You're all set 🎉" : "Get started"}
        description={
          allDone
            ? "You've studied your first session. Keep the streak going!"
            : `${doneCount} of ${required.length} steps done`
        }
        action={
          <Button
            variant="ghost"
            onClick={() => update.mutate({ onboarding_state: "complete" })}
            loading={update.isPending && update.variables?.onboarding_state === "complete"}
          >
            {allDone ? "Done" : "Skip guide"}
          </Button>
        }
      />
      <div
        role="progressbar"
        aria-label="Setup progress"
        aria-valuemin={0}
        aria-valuemax={required.length}
        aria-valuenow={doneCount}
        className="h-1.5 overflow-hidden rounded-full bg-slate-100"
      >
        <div className="h-full rounded-full bg-brand-600 transition-[width]" style={{ width: `${(doneCount / required.length) * 100}%` }} />
      </div>
      <ol className="space-y-2">
        {steps.map((step, i) => (
          <li
            key={step.key}
            data-testid={`onboarding-step-${step.key}`}
            data-done={step.done}
            className={cn(
              "flex flex-wrap items-center gap-3 rounded-lg px-3 py-2 ring-1 ring-inset",
              step === next ? "bg-brand-50 ring-brand-200" : "ring-slate-200",
            )}
          >
            <span
              aria-hidden
              className={cn(
                "grid size-7 shrink-0 place-items-center rounded-full text-xs font-semibold",
                step.done ? "bg-emerald-600 text-white" : "bg-slate-100 text-slate-600",
              )}
            >
              {step.done ? "✓" : i + 1}
            </span>
            <div className="min-w-0 flex-1">
              <p className={cn("text-sm font-medium", step.done ? "text-slate-500 line-through" : "text-slate-900")}>
                {step.title}
                {step.optional && <span className="ml-1 text-xs font-normal text-slate-600">(optional)</span>}
                <span className="sr-only">{step.done ? " — done" : ""}</span>
              </p>
              <p className="text-xs text-slate-600">{step.waiting ?? step.description}</p>
            </div>
            {!step.done &&
              (step.waiting ? (
                <span className="text-xs text-slate-600" aria-live="polite">
                  {step.waiting}
                </span>
              ) : (
                <Link
                  href={step.href}
                  className={cn(
                    "rounded-lg px-3 py-1.5 text-sm font-medium",
                    step === next ? "bg-brand-600 text-white hover:bg-brand-700" : "text-brand-700 hover:bg-brand-50",
                  )}
                >
                  {step.cta}
                </Link>
              ))}
          </li>
        ))}
      </ol>
    </Card>
  );
}
