"use client";

import Link from "next/link";
import { useState } from "react";

import { Button } from "@/components/ui/button";
import { Card, CardHeader } from "@/components/ui/card";
import { Alert, Badge, Skeleton } from "@/components/ui/feedback";
import { useSessions } from "@/hooks/use-sessions";
import { cn, formatPercent } from "@/lib/utils";
import type { SessionListItem, SessionStatus } from "@/types/session";

const STATUS: Record<SessionStatus, { label: string; tone: string }> = {
  initialising: { label: "Not started", tone: "bg-slate-100 text-slate-600" },
  active: { label: "In progress", tone: "bg-brand-50 text-brand-700" },
  paused: { label: "Paused", tone: "bg-amber-50 text-amber-700" },
  completed: { label: "Completed", tone: "bg-emerald-50 text-emerald-700" },
  abandoned: { label: "Abandoned", tone: "bg-slate-100 text-slate-500" },
};

const TYPE_LABEL: Record<string, string> = {
  mixed: "Mixed",
  teach: "Learn",
  practice: "Practice",
  review: "Review",
};

function when(iso: string | null): string {
  if (!iso) return "—";
  return new Date(iso).toLocaleString(undefined, {
    day: "numeric",
    month: "short",
    hour: "numeric",
    minute: "2-digit",
  });
}

function Row({ session }: { session: SessionListItem }) {
  const status = STATUS[session.status] ?? STATUS.active;
  const running = ["initialising", "active", "paused"].includes(session.status);
  const minutes = session.duration_seconds
    ? Math.max(1, Math.round(session.duration_seconds / 60))
    : session.summary
      ? Math.max(1, Math.round(session.summary.duration_minutes))
      : null;
  return (
    <li data-testid="session-history-item" className="flex flex-wrap items-center justify-between gap-3 py-3">
      <div className="min-w-0">
        <p className="flex flex-wrap items-center gap-2 text-sm font-medium text-slate-900">
          {session.concepts.length > 0 ? session.concepts.join(", ") : "Study session"}
          <Badge className={status.tone}>{status.label}</Badge>
        </p>
        <p className="mt-0.5 text-xs text-slate-500">
          {TYPE_LABEL[session.session_type] ?? session.session_type} · {when(session.started_at)}
          {minutes !== null && ` · ${minutes} min`}
          {session.summary && session.summary.questions_answered > 0 &&
            ` · ${session.summary.questions_answered} questions, ${formatPercent(session.summary.accuracy)} correct`}
        </p>
      </div>
      <Link
        href={`/session/${session.id}`}
        className={cn(
          "rounded-lg px-3 py-1.5 text-sm font-medium",
          running ? "bg-brand-600 text-white hover:bg-brand-700" : "text-brand-700 hover:bg-brand-50",
        )}
      >
        {running ? "Continue" : "View summary"}
      </Link>
    </li>
  );
}

export function SessionHistory() {
  const [page, setPage] = useState(1);
  const { data, isLoading, error } = useSessions({ page, per_page: 10 });
  const items = data?.data ?? [];
  const pagination = data?.meta.pagination;
  return (
    <Card data-testid="session-history">
      <CardHeader title="Your sessions" description="Pick up where you left off or revisit a summary." />
      {isLoading ? (
        <Skeleton className="h-24 w-full" />
      ) : error ? (
        <Alert tone="error">Could not load your sessions. {error.message}</Alert>
      ) : items.length === 0 ? (
        <p className="text-sm text-slate-500">No sessions yet — start your first one above.</p>
      ) : (
        <>
          <ul className="divide-y divide-slate-100">
            {items.map((s) => (
              <Row key={s.id} session={s} />
            ))}
          </ul>
          {pagination && pagination.total_pages > 1 && (
            <div className="mt-3 flex items-center justify-between text-sm text-slate-600">
              <Button variant="ghost" disabled={page <= 1} onClick={() => setPage((p) => p - 1)}>
                Previous
              </Button>
              <span>
                Page {pagination.page} of {pagination.total_pages}
              </span>
              <Button
                variant="ghost"
                disabled={page >= pagination.total_pages}
                onClick={() => setPage((p) => p + 1)}
              >
                Next
              </Button>
            </div>
          )}
        </>
      )}
    </Card>
  );
}
