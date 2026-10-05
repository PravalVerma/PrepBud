"use client";

import Link from "next/link";
import { useState } from "react";

import { Button } from "@/components/ui/button";
import { Card, CardHeader } from "@/components/ui/card";
import { Alert, Badge, Skeleton } from "@/components/ui/feedback";
import { useMisconceptions } from "@/hooks/use-dashboard";
import { cn, formatDate } from "@/lib/utils";
import type { MisconceptionStatus, StudentMisconception } from "@/types/dashboard";

const FILTERS: { value: MisconceptionStatus | "all"; label: string }[] = [
  { value: "all", label: "All" },
  { value: "active", label: "Active" },
  { value: "recurring", label: "Recurring" },
  { value: "resolved", label: "Resolved" },
];

const STATUS: Record<MisconceptionStatus, { label: string; tone: string }> = {
  active: { label: "Active", tone: "bg-amber-50 text-amber-700" },
  recurring: { label: "Back again", tone: "bg-red-50 text-red-700" },
  resolved: { label: "Resolved", tone: "bg-emerald-50 text-emerald-700" },
};

function Item({ item }: { item: StudentMisconception }) {
  const [open, setOpen] = useState(false);
  const status = STATUS[item.status] ?? STATUS.active;
  return (
    <li data-testid="misconception-item" data-status={item.status} className="py-3">
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div className="min-w-0">
          <p className="flex flex-wrap items-center gap-2 text-sm font-medium text-slate-900">
            {item.misconception.name}
            <Badge className={status.tone}>{status.label}</Badge>
          </p>
          <p className="mt-0.5 text-sm text-slate-600">{item.misconception.description}</p>
          <p className="mt-1 text-xs text-slate-500">
            <Link href={`/concepts/${item.misconception.concept_id}`} className="text-brand-700 hover:underline">
              {item.misconception.concept_name}
            </Link>{" "}
            · seen {item.occurrence_count}× · first {formatDate(item.detected_at)}
            {item.resolved_at && ` · resolved ${formatDate(item.resolved_at)}`}
          </p>
        </div>
        {item.evidence.length > 0 && (
          <Button variant="ghost" aria-expanded={open} onClick={() => setOpen((o) => !o)}>
            {open ? "Hide evidence" : "Evidence"}
          </Button>
        )}
      </div>
      {open && (
        <ul className="mt-2 space-y-1 rounded-lg bg-slate-50 p-3 text-xs text-slate-600">
          {item.evidence.map((e, i) => (
            <li key={`${e.detected_at}-${i}`}>
              {e.description || "Detected in an answer"}
              {e.detected_at && <span className="text-slate-500"> — {formatDate(e.detected_at)}</span>}
            </li>
          ))}
        </ul>
      )}
    </li>
  );
}

export function MisconceptionList() {
  const [filter, setFilter] = useState<MisconceptionStatus | "all">("all");
  const [page, setPage] = useState(1);
  const { data, isLoading, error } = useMisconceptions({
    status: filter === "all" ? undefined : filter,
    page,
    per_page: 20,
  });
  const items = data?.data ?? [];
  const pages = data?.meta.pagination.total_pages ?? 1;
  return (
    <Card id="misconceptions" data-testid="misconception-list">
      <CardHeader
        title="Misconceptions"
        description="Wrong mental models your tutor spotted, and whether they're fixed."
      />
      <div role="tablist" aria-label="Filter misconceptions" className="mb-3 flex flex-wrap gap-1 text-sm">
        {FILTERS.map((f) => (
          <button
            key={f.value}
            type="button"
            role="tab"
            aria-selected={filter === f.value}
            onClick={() => {
              setFilter(f.value);
              setPage(1);
            }}
            className={cn(
              "rounded-full px-3 py-1",
              filter === f.value ? "bg-brand-600 text-white" : "bg-slate-100 text-slate-700 hover:bg-slate-200",
            )}
          >
            {f.label}
          </button>
        ))}
      </div>
      {isLoading ? (
        <Skeleton className="h-24 w-full" />
      ) : error ? (
        <Alert tone="error">Could not load misconceptions.</Alert>
      ) : items.length === 0 ? (
        <p className="text-sm text-slate-500">
          {filter === "all" ? "None detected yet — they show up when an answer reveals a mix-up." : "Nothing here."}
        </p>
      ) : (
        <ul className="divide-y divide-slate-100">
          {items.map((item) => (
            <Item key={item.id} item={item} />
          ))}
        </ul>
      )}
      {pages > 1 && (
        <div className="mt-3 flex justify-between">
          <Button variant="ghost" disabled={page <= 1} onClick={() => setPage((p) => p - 1)}>
            Previous
          </Button>
          <Button variant="ghost" disabled={page >= pages} onClick={() => setPage((p) => p + 1)}>
            Next
          </Button>
        </div>
      )}
    </Card>
  );
}
