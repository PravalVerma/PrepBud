"use client";

import Link from "next/link";

import { Button } from "@/components/ui/button";
import { Card, CardHeader } from "@/components/ui/card";
import { Alert, Badge, Skeleton } from "@/components/ui/feedback";
import { startErrorMessage, useStartSession } from "@/hooks/use-sessions";
import { useRegeneratePlan, useStudyPlan, useUpdateReviewItem } from "@/hooks/use-study-plan";
import { addDays, cn, dayLabel, formatDate, formatPercent, isoDay } from "@/lib/utils";
import type { ReviewItem } from "@/types/study";

interface Group {
  key: string;
  label: string;
  items: ReviewItem[];
}

/** Overdue first (AC-6.2), then one group per day, finished items last. */
export function groupPlan(items: ReviewItem[], today: string): Group[] {
  const groups: Group[] = [];
  const overdue = items.filter((i) => i.status === "overdue");
  if (overdue.length) groups.push({ key: "overdue", label: "Overdue", items: overdue });
  const byDay = new Map<string, ReviewItem[]>();
  for (const item of items) {
    if (item.status !== "pending") continue;
    const day = item.scheduled_date < today ? today : item.scheduled_date;
    byDay.set(day, [...(byDay.get(day) ?? []), item]);
  }
  for (const day of [...byDay.keys()].sort()) {
    groups.push({ key: day, label: dayLabel(day, today), items: byDay.get(day)! });
  }
  const done = items.filter((i) => i.status === "completed" || i.status === "skipped");
  if (done.length) groups.push({ key: "done", label: "Done today", items: done });
  return groups;
}

function ItemRow({ item, today }: { item: ReviewItem; today: string }) {
  const update = useUpdateReviewItem();
  const session = useStartSession();
  const open = item.status === "pending" || item.status === "overdue";
  const busy = update.isPending || session.isPending;
  return (
    <li
      data-testid="plan-item"
      data-status={item.status}
      data-kind={item.kind}
      className="flex flex-wrap items-center justify-between gap-3 py-3"
    >
      <div className="min-w-0">
        <p className="flex flex-wrap items-center gap-2 text-sm font-medium text-slate-900">
          <Link
            href={`/concepts/${item.concept_id}`}
            className={cn("hover:underline", item.status === "skipped" && "text-slate-400 line-through")}
          >
            {item.concept_name}
          </Link>
          <Badge className={item.kind === "review" ? "bg-amber-50 text-amber-700" : "bg-brand-50 text-brand-700"}>
            {item.kind === "review" ? "Review" : "Learn"}
          </Badge>
          {item.status === "completed" && <Badge className="bg-emerald-50 text-emerald-700">✓ Done</Badge>}
          {item.status === "skipped" && <Badge>Skipped</Badge>}
        </p>
        <p className="mt-0.5 text-xs text-slate-500">
          Mastery {formatPercent(item.mastery_level)} · {item.mastery_label}
          {item.status === "overdue" && ` · due ${dayLabel(item.scheduled_date, today).toLowerCase()}`}
          {item.next_review_at && item.status === "completed" && ` · next review ${formatDate(item.next_review_at)}`}
        </p>
      </div>
      <div className="flex flex-wrap gap-1">
        {open ? (
          <>
            <Button
              variant="secondary"
              disabled={busy}
              onClick={() =>
                session.start({
                  session_type: item.kind === "review" ? "review" : "teach",
                  time_budget_minutes: 15,
                  concept_ids: [item.concept_id],
                })
              }
            >
              Study
            </Button>
            <Button
              variant="ghost"
              disabled={busy}
              onClick={() => update.mutate({ id: item.id, body: { scheduled_date: addDays(today, 1) } })}
              title="Move to tomorrow"
            >
              Later
            </Button>
            <Button variant="ghost" disabled={busy} onClick={() => update.mutate({ id: item.id, body: { status: "skipped" } })}>
              Skip
            </Button>
          </>
        ) : (
          item.status === "skipped" && (
            <Button variant="ghost" disabled={busy} onClick={() => update.mutate({ id: item.id, body: { status: "pending" } })}>
              Undo
            </Button>
          )
        )}
      </div>
      {(update.error || session.error) && (
        <div className="w-full">
          <Alert tone="error">{update.error?.message ?? startErrorMessage(session.error)}</Alert>
        </div>
      )}
    </li>
  );
}

export function StudyPlanView() {
  const { data: plan, isLoading, error } = useStudyPlan();
  const regenerate = useRegeneratePlan();
  const today = isoDay(new Date());

  return (
    <Card data-testid="study-plan">
      <CardHeader
        title="Study plan"
        description={
          plan
            ? `${plan.stats.total_items} item${plan.stats.total_items === 1 ? "" : "s"} · about ${plan.stats.estimated_minutes} min of study`
            : undefined
        }
        action={
          <Button variant="ghost" onClick={() => regenerate.mutate()} loading={regenerate.isPending}>
            Rebuild plan
          </Button>
        }
      />
      {isLoading ? (
        <Skeleton className="h-40 w-full" />
      ) : error || !plan ? (
        <Alert tone="error">Could not load your study plan. {error?.message}</Alert>
      ) : plan.items.length === 0 ? (
        <p className="text-sm text-slate-500">
          Your plan is empty.{" "}
          <Link href="/goals" className="font-medium text-brand-700 hover:underline">
            Set a goal
          </Link>{" "}
          or study a concept — reviews are scheduled automatically after each session.
        </p>
      ) : (
        <div className="space-y-5">
          {groupPlan(plan.items, today).map((group) => (
            <section key={group.key} data-testid={`plan-group-${group.key}`}>
              <h3
                className={cn(
                  "text-xs font-semibold uppercase tracking-wide",
                  group.key === "overdue" ? "text-red-600" : "text-slate-500",
                )}
              >
                {group.label} · {group.items.length}
              </h3>
              <ul className="divide-y divide-slate-100">
                {group.items.map((item) => (
                  <ItemRow key={item.id} item={item} today={today} />
                ))}
              </ul>
            </section>
          ))}
        </div>
      )}
      {regenerate.error && <Alert tone="error">{regenerate.error.message}</Alert>}
    </Card>
  );
}
