"use client";

import Link from "next/link";
import { useState } from "react";

import { GoalForm } from "@/components/goals/goal-form";
import { Button } from "@/components/ui/button";
import { Card, CardHeader } from "@/components/ui/card";
import { Alert, Badge, Skeleton } from "@/components/ui/feedback";
import { useDeleteGoal, useGoals, useUpdateGoal } from "@/hooks/use-study-plan";
import { cn, formatPercent, parseDay } from "@/lib/utils";
import type { Goal, GoalStatus } from "@/types/study";

const STATUS: Record<GoalStatus, { label: string; tone: string }> = {
  draft: { label: "Draft", tone: "bg-slate-100 text-slate-600" },
  active: { label: "Active", tone: "bg-brand-50 text-brand-700" },
  paused: { label: "Paused", tone: "bg-amber-50 text-amber-700" },
  completed: { label: "Completed", tone: "bg-emerald-50 text-emerald-700" },
  abandoned: { label: "Abandoned", tone: "bg-slate-100 text-slate-500" },
};

function deadline(goal: Goal): string | null {
  if (!goal.target_date) return null;
  const date = parseDay(goal.target_date).toLocaleDateString(undefined, { day: "numeric", month: "short" });
  const days = goal.progress.days_remaining;
  if (days === null) return `by ${date}`;
  if (days < 0) return `${date} · ${-days} day${days === -1 ? "" : "s"} past`;
  if (days === 0) return `${date} · due today`;
  return `${date} · ${days} day${days === 1 ? "" : "s"} left`;
}

function GoalCard({ goal }: { goal: Goal }) {
  const update = useUpdateGoal();
  const remove = useDeleteGoal();
  const [editing, setEditing] = useState(false);
  const [confirmDelete, setConfirmDelete] = useState(false);
  const { progress } = goal;
  const status = STATUS[goal.status] ?? STATUS.active;
  const setStatus = (next: GoalStatus) => update.mutate({ id: goal.id, body: { status: next } });
  const due = deadline(goal);

  if (editing) {
    return (
      <Card>
        <CardHeader title="Edit goal" action={<Button variant="ghost" onClick={() => setEditing(false)}>Cancel</Button>} />
        <GoalForm goal={goal} onDone={() => setEditing(false)} />
      </Card>
    );
  }
  return (
    <Card data-testid="goal-card" className="space-y-4">
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div className="min-w-0">
          <h3 className="flex flex-wrap items-center gap-2 text-base font-semibold text-slate-900">
            {goal.title}
            <Badge className={status.tone}>{status.label}</Badge>
          </h3>
          {goal.description && <p className="mt-1 text-sm text-slate-600">{goal.description}</p>}
          {due && (
            <p className={cn("mt-1 text-xs", progress.days_remaining !== null && progress.days_remaining < 0 ? "text-red-600" : "text-slate-500")}>
              {due}
            </p>
          )}
        </div>
        {goal.status === "active" && (
          <Link
            href={`/session?goal_id=${goal.id}`}
            className="rounded-lg bg-brand-600 px-3 py-1.5 text-sm font-medium text-white hover:bg-brand-700"
          >
            Study now
          </Link>
        )}
      </div>

      <div className="space-y-1.5" data-testid="goal-progress">
        <div className="flex justify-between text-xs text-slate-600">
          <span>
            {progress.mastered_count} of {progress.concept_count} concept{progress.concept_count === 1 ? "" : "s"} mastered
          </span>
          <span>{formatPercent(progress.progress)}</span>
        </div>
        <div
          role="progressbar"
          aria-label={`Progress on ${goal.title}`}
          aria-valuemin={0}
          aria-valuemax={100}
          aria-valuenow={Math.round(progress.progress * 100)}
          className="h-2.5 overflow-hidden rounded-full bg-slate-100"
        >
          <div
            className={cn("h-full rounded-full transition-[width] duration-700", progress.all_mastered ? "bg-emerald-500" : "bg-brand-600")}
            style={{ width: `${Math.max(2, Math.round(progress.progress * 100))}%` }}
          />
        </div>
        {progress.all_mastered && goal.status === "active" && (
          <p className="text-xs text-emerald-700">
            Everything in this goal is mastered.{" "}
            <button type="button" className="font-medium underline" onClick={() => setStatus("completed")}>
              Mark it complete
            </button>
          </p>
        )}
      </div>

      <div className="flex flex-wrap gap-2">
        {goal.status === "active" && (
          <Button variant="secondary" onClick={() => setStatus("paused")} disabled={update.isPending}>
            Pause
          </Button>
        )}
        {(goal.status === "paused" || goal.status === "completed" || goal.status === "abandoned") && (
          <Button variant="secondary" onClick={() => setStatus("active")} disabled={update.isPending}>
            Resume
          </Button>
        )}
        {goal.status !== "completed" && (
          <Button variant="ghost" onClick={() => setStatus("completed")} disabled={update.isPending}>
            Complete
          </Button>
        )}
        <Button variant="ghost" onClick={() => setEditing(true)}>
          Edit
        </Button>
        {confirmDelete ? (
          <>
            <Button variant="ghost" onClick={() => setConfirmDelete(false)}>
              Keep
            </Button>
            <Button
              className="bg-red-600 hover:bg-red-700"
              onClick={() => remove.mutate(goal.id)}
              loading={remove.isPending}
            >
              Delete goal
            </Button>
          </>
        ) : (
          <Button variant="ghost" onClick={() => setConfirmDelete(true)}>
            Delete
          </Button>
        )}
      </div>
      {(update.error || remove.error) && <Alert tone="error">{(update.error ?? remove.error)?.message}</Alert>}
    </Card>
  );
}

export function GoalList() {
  const [filter, setFilter] = useState<"active" | "all">("active");
  const { data, isLoading, error } = useGoals({ per_page: 50, status: filter === "active" ? "active" : undefined });
  const goals = data?.data ?? [];
  return (
    <section className="space-y-4" data-testid="goal-list">
      <div className="flex items-center justify-between gap-3">
        <h2 className="text-base font-semibold text-slate-900">Your goals</h2>
        <div role="tablist" aria-label="Filter goals" className="flex rounded-lg bg-slate-100 p-0.5 text-sm">
          {(["active", "all"] as const).map((f) => (
            <button
              key={f}
              type="button"
              role="tab"
              aria-selected={filter === f}
              onClick={() => setFilter(f)}
              className={cn("rounded-md px-3 py-1", filter === f ? "bg-white font-medium shadow-sm" : "text-slate-600")}
            >
              {f === "active" ? "Active" : "All"}
            </button>
          ))}
        </div>
      </div>
      {isLoading ? (
        <Skeleton className="h-32 w-full" />
      ) : error ? (
        <Alert tone="error">Could not load your goals. {error.message}</Alert>
      ) : goals.length === 0 ? (
        <Card>
          <p className="text-sm text-slate-500">
            {filter === "active" ? "No active goals. Create one above to get a study plan." : "No goals yet."}
          </p>
        </Card>
      ) : (
        goals.map((g) => <GoalCard key={g.id} goal={g} />)
      )}
    </section>
  );
}
