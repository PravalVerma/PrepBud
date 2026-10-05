import Link from "next/link";

import { MasteryBar } from "@/components/session/mastery-bar";
import { Markdown } from "@/components/session/markdown";
import { Card, CardHeader } from "@/components/ui/card";
import { formatDate, formatPercent } from "@/lib/utils";
import type { SessionSummary } from "@/types/session";

export const END_REASON_LABEL: Record<string, string> = {
  student_ended: "You ended the session",
  concepts_complete: "All planned concepts covered",
  all_mastered: "Everything in this session is mastered",
  time_budget: "Time budget reached",
  interaction_limit: "Interaction limit reached",
  frustration: "Time for a break",
  token_budget: "AI usage limit for this session reached",
  ai_budget_exceeded: "Daily AI usage limit reached",
  ai_unavailable: "The AI tutor became unavailable",
};

function Stat({ label, value }: { label: string; value: string }) {
  return (
    <div className="rounded-lg bg-slate-50 px-3 py-2">
      <dt className="text-xs text-slate-500">{label}</dt>
      <dd className="text-lg font-semibold text-slate-900">{value}</dd>
    </div>
  );
}

export function SessionSummaryCard({
  summary,
  conceptNames = {},
}: {
  summary: SessionSummary;
  conceptNames?: Record<string, string>;
}) {
  const reason = summary.end_reason ? END_REASON_LABEL[summary.end_reason] : null;
  return (
    <Card data-testid="session-summary" className="space-y-5">
      <CardHeader title="Session summary" description={reason ?? undefined} />
      {summary.text && <Markdown className="text-base">{summary.text}</Markdown>}
      <dl className="grid grid-cols-2 gap-3 sm:grid-cols-4">
        <Stat label="Duration" value={`${Math.max(1, Math.round(summary.duration_minutes))} min`} />
        <Stat label="Questions" value={String(summary.questions_answered)} />
        <Stat label="Accuracy" value={summary.questions_answered ? formatPercent(summary.accuracy) : "—"} />
        <Stat label="Concepts" value={String(summary.concepts_covered)} />
      </dl>
      {summary.mastery_changes.length > 0 && (
        <div className="space-y-3">
          <h3 className="text-sm font-semibold text-slate-900">Mastery</h3>
          {summary.mastery_changes.map((c) => (
            <MasteryBar key={c.concept_id ?? c.concept} name={c.concept} value={c.to} previous={c.from} />
          ))}
        </div>
      )}
      {(summary.reviews?.length ?? 0) > 0 && (
        <div>
          <h3 className="text-sm font-semibold text-slate-900">Next reviews</h3>
          <ul className="mt-1 space-y-1 text-sm text-slate-600">
            {summary.reviews!.map((r) => (
              <li key={r.concept_id} className="flex justify-between gap-3">
                <span>{conceptNames[r.concept_id] ?? "Concept"}</span>
                <span>{formatDate(r.next_review_date)}</span>
              </li>
            ))}
          </ul>
        </div>
      )}
      <div className="flex flex-wrap gap-2">
        <Link
          href="/session"
          className="rounded-lg bg-brand-600 px-4 py-2 text-sm font-medium text-white hover:bg-brand-700"
        >
          Start another session
        </Link>
        <Link
          href="/concepts"
          className="rounded-lg px-4 py-2 text-sm font-medium text-slate-700 ring-1 ring-inset ring-slate-300 hover:bg-slate-50"
        >
          Browse concepts
        </Link>
      </div>
    </Card>
  );
}
