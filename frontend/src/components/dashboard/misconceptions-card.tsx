"use client";

import Link from "next/link";

import { Card, CardHeader } from "@/components/ui/card";
import { Badge, Skeleton } from "@/components/ui/feedback";
import { useMisconceptions } from "@/hooks/use-dashboard";

/** Open misconceptions (active or recurring) — the things worth revisiting. */
export function MisconceptionsCard() {
  const active = useMisconceptions({ status: "active", per_page: 5 });
  const recurring = useMisconceptions({ status: "recurring", per_page: 5 });
  const items = [...(recurring.data?.data ?? []), ...(active.data?.data ?? [])].slice(0, 5);
  const total = (active.data?.meta.pagination.total ?? 0) + (recurring.data?.meta.pagination.total ?? 0);
  return (
    <Card data-testid="misconceptions-card">
      <CardHeader
        title="Common mix-ups"
        description={total ? `${total} to work on` : "Misconceptions your tutor has spotted."}
        action={
          <Link href="/progress#misconceptions" className="text-sm font-medium text-brand-700 hover:underline">
            All
          </Link>
        }
      />
      {active.isLoading || recurring.isLoading ? (
        <Skeleton className="h-16 w-full" />
      ) : items.length === 0 ? (
        <p className="text-sm text-slate-500">None right now — nice work.</p>
      ) : (
        <ul className="space-y-2 text-sm">
          {items.map((m) => (
            <li key={m.id} className="flex items-start justify-between gap-3">
              <div className="min-w-0">
                <p className="font-medium text-slate-800">{m.misconception.name}</p>
                <Link href={`/concepts/${m.misconception.concept_id}`} className="text-xs text-brand-700 hover:underline">
                  {m.misconception.concept_name}
                </Link>
              </div>
              <Badge className={m.status === "recurring" ? "bg-red-50 text-red-700" : "bg-amber-50 text-amber-700"}>
                {m.status === "recurring" ? "Back again" : `×${m.occurrence_count}`}
              </Badge>
            </li>
          ))}
        </ul>
      )}
    </Card>
  );
}
