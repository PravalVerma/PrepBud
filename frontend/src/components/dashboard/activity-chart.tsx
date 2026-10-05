"use client";

import { Card, CardHeader } from "@/components/ui/card";
import { Skeleton } from "@/components/ui/feedback";
import { useMasteryOverview } from "@/hooks/use-dashboard";
import { formatMinutes } from "@/lib/mastery";
import { cn, dayLabel, parseDay } from "@/lib/utils";
import type { DayActivity } from "@/types/dashboard";

const HEIGHT = 96;

function describe(day: DayActivity): string {
  const parts = [`${dayLabel(day.date)}: ${formatMinutes(day.minutes)} studied`];
  if (day.questions) parts.push(`${day.questions} question${day.questions === 1 ? "" : "s"}`);
  if (day.concepts_practiced) parts.push(`${day.concepts_practiced} concept${day.concepts_practiced === 1 ? "" : "s"}`);
  return parts.join(", ");
}

/** Activity history: study minutes per day (last 30 days) with a readable summary. */
export function ActivityChart({ days = 30 }: { days?: number }) {
  const { data, isLoading } = useMasteryOverview(days);
  if (isLoading || !data) {
    return (
      <Card>
        <CardHeader title="Activity" />
        <Skeleton className="h-28 w-full" />
      </Card>
    );
  }
  const series = data.recent_activity;
  const max = Math.max(10, ...series.map((d) => d.minutes));
  const total = series.reduce((sum, d) => sum + d.minutes, 0);
  const activeDays = series.filter((d) => d.minutes > 0 || d.questions > 0).length;
  return (
    <Card data-testid="activity-chart">
      <CardHeader
        title="Activity"
        description={`${formatMinutes(total)} over the last ${days} days · ${activeDays} active day${activeDays === 1 ? "" : "s"}`}
      />
      <div
        role="img"
        aria-label={`Study minutes per day for the last ${days} days; ${activeDays} active days, ${formatMinutes(total)} in total`}
        className="flex h-24 items-end gap-[3px]"
      >
        {series.map((day) => {
          const active = day.minutes > 0 || day.questions > 0;
          const height = day.minutes > 0 ? Math.max(6, (day.minutes / max) * HEIGHT) : active ? 6 : 3;
          return (
            <div
              key={day.date}
              title={describe(day)}
              data-testid="activity-bar"
              data-active={active}
              className={cn("flex-1 rounded-sm", active ? "bg-brand-500" : "bg-slate-200")}
              style={{ height }}
            />
          );
        })}
      </div>
      <div className="mt-1 flex justify-between text-[10px] text-slate-500">
        <span>{parseDay(series[0].date).toLocaleDateString(undefined, { day: "numeric", month: "short" })}</span>
        <span>Today</span>
      </div>
    </Card>
  );
}
