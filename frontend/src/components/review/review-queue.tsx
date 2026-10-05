"use client";

import Link from "next/link";

import { Button } from "@/components/ui/button";
import { Card, CardHeader } from "@/components/ui/card";
import { Alert, Skeleton } from "@/components/ui/feedback";
import { startErrorMessage, useStartSession } from "@/hooks/use-sessions";
import { useStudyPlan } from "@/hooks/use-study-plan";
import { dayLabel, isoDay } from "@/lib/utils";

const MAX_CONCEPTS = 5;

/** "X items due today" with one-click review / study sessions (AC-6.6). */
export function ReviewQueue({ compact = false }: { compact?: boolean }) {
  const { data: plan, isLoading, error } = useStudyPlan();
  const session = useStartSession();

  if (isLoading) {
    return (
      <Card>
        <Skeleton className="h-16 w-full" />
      </Card>
    );
  }
  if (error || !plan) {
    return (
      <Card>
        <CardHeader title="Due for review" />
        <p className="text-sm text-slate-500">The study plan is unavailable right now.</p>
      </Card>
    );
  }

  const today = isoDay(new Date());
  const { stats } = plan;
  const due = plan.items.filter(
    (i) => (i.status === "pending" || i.status === "overdue") && i.scheduled_date <= today,
  );
  const learnToday = due.filter((i) => i.kind === "learn").map((i) => i.concept_id);
  const headline =
    stats.due_today === 0
      ? "You're all caught up"
      : `${stats.due_today} item${stats.due_today === 1 ? "" : "s"} due today`;

  return (
    <Card data-testid="review-queue">
      <CardHeader
        title={compact ? "Due for review" : "Today"}
        description={
          stats.due_today === 0
            ? stats.next_due_date
              ? `Next item ${dayLabel(stats.next_due_date, today).toLowerCase()}.`
              : "Nothing scheduled — set a goal to get a plan."
            : [
                stats.overdue > 0 ? `${stats.overdue} overdue` : null,
                stats.reviews_due > 0 ? `${stats.reviews_due} review${stats.reviews_due === 1 ? "" : "s"}` : null,
                learnToday.length > 0 ? `${learnToday.length} to learn` : null,
              ]
                .filter(Boolean)
                .join(" · ")
        }
        action={
          compact ? (
            <Link href="/review" className="text-sm font-medium text-brand-700 hover:underline">
              Plan
            </Link>
          ) : undefined
        }
      />
      <p className="text-2xl font-semibold text-slate-900" data-testid="due-count">
        {headline}
      </p>
      <div className="mt-4 flex flex-wrap gap-2">
        <Button
          onClick={() => session.start({ session_type: "review", time_budget_minutes: 15 })}
          loading={session.isPending && session.variables?.session_type === "review"}
          variant={stats.reviews_due > 0 ? "primary" : "secondary"}
        >
          {stats.reviews_due > 0 ? "Start review" : "Review anyway"}
        </Button>
        {learnToday.length > 0 && (
          <Button
            variant={stats.reviews_due > 0 ? "secondary" : "primary"}
            onClick={() =>
              session.start({
                session_type: "mixed",
                time_budget_minutes: 30,
                concept_ids: learnToday.slice(0, MAX_CONCEPTS),
              })
            }
            loading={session.isPending && session.variables?.session_type === "mixed"}
          >
            Study today&apos;s plan
          </Button>
        )}
      </div>
      {session.error && (
        <div className="mt-3">
          <Alert tone="error">{startErrorMessage(session.error)}</Alert>
        </div>
      )}
    </Card>
  );
}
