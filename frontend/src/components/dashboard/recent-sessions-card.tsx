"use client";

import Link from "next/link";

import { Card, CardHeader } from "@/components/ui/card";
import { Skeleton } from "@/components/ui/feedback";
import { useSessions } from "@/hooks/use-sessions";
import { formatPercent } from "@/lib/utils";

export function RecentSessionsCard() {
  const { data, isLoading, error } = useSessions({ per_page: 3 });
  const items = data?.data ?? [];
  return (
    <Card data-testid="recent-sessions">
      <CardHeader
        title="Recent sessions"
        description="Pick up where you left off."
        action={
          <Link href="/session" className="text-sm font-medium text-brand-700 hover:underline">
            Start
          </Link>
        }
      />
      {isLoading ? (
        <Skeleton className="h-12 w-full" />
      ) : error ? (
        <p className="text-sm text-slate-500">Sessions are unavailable right now.</p>
      ) : items.length === 0 ? (
        <p className="text-sm text-slate-500">No sessions yet.</p>
      ) : (
        <ul className="space-y-2 text-sm">
          {items.map((s) => (
            <li key={s.id} className="flex items-center justify-between gap-3">
              <Link href={`/session/${s.id}`} className="truncate font-medium text-brand-700 hover:underline">
                {s.concepts.join(", ") || "Study session"}
              </Link>
              <span className="shrink-0 text-xs text-slate-500">
                {s.status === "completed" && s.summary?.questions_answered
                  ? `${formatPercent(s.summary.accuracy)} correct`
                  : s.status}
              </span>
            </li>
          ))}
        </ul>
      )}
    </Card>
  );
}
