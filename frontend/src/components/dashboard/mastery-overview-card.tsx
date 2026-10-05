"use client";

import Link from "next/link";

import { Card, CardHeader } from "@/components/ui/card";
import { Alert, Skeleton } from "@/components/ui/feedback";
import { useMasteryOverview } from "@/hooks/use-dashboard";
import { MASTERY_BG, MASTERY_LABELS, formatMinutes, masteryBg } from "@/lib/mastery";
import { cn, formatPercent } from "@/lib/utils";

function Stat({ label, value, hint }: { label: string; value: string; hint?: string }) {
  return (
    <div className="rounded-lg bg-slate-50 px-3 py-2">
      <dt className="text-xs text-slate-500">{label}</dt>
      <dd className="text-xl font-semibold text-slate-900">{value}</dd>
      {hint && <dd className="text-xs text-slate-500">{hint}</dd>}
    </div>
  );
}

/** AC-7.1: overall progress, the mastery distribution and per-subject mastery. */
export function MasteryOverviewCard() {
  const { data, isLoading, error } = useMasteryOverview(30);

  if (isLoading) {
    return (
      <Card className="lg:col-span-2">
        <CardHeader title="Mastery overview" />
        <Skeleton className="h-40 w-full" />
      </Card>
    );
  }
  if (error || !data) {
    return (
      <Card className="lg:col-span-2">
        <CardHeader title="Mastery overview" />
        <Alert tone="error">Progress is unavailable right now.</Alert>
      </Card>
    );
  }

  const s = data.overall_stats;
  const total = s.total_concepts;
  return (
    <Card className="space-y-5 lg:col-span-2" data-testid="mastery-overview">
      <CardHeader
        title="Mastery overview"
        description="Your progress across every concept."
        action={
          <Link href="/progress" className="text-sm font-medium text-brand-700 hover:underline">
            Details
          </Link>
        }
      />
      <dl className="grid grid-cols-2 gap-3 sm:grid-cols-4">
        <Stat label="Mastered" value={`${s.total_mastered} / ${total}`} hint={`${s.in_progress} in progress`} />
        <Stat label="Average mastery" value={formatPercent(s.avg_mastery)} />
        <Stat label="Streak" value={`${s.streak_days} day${s.streak_days === 1 ? "" : "s"}`} />
        <Stat
          label="Study time"
          value={formatMinutes(s.total_study_minutes)}
          hint={`${s.total_sessions} session${s.total_sessions === 1 ? "" : "s"}`}
        />
      </dl>

      {total > 0 && (
        <div>
          <h3 className="mb-2 text-sm font-semibold text-slate-900">Concepts by mastery</h3>
          <div className="flex h-3 overflow-hidden rounded-full bg-slate-100" role="img" aria-label="Concepts by mastery level">
            {MASTERY_LABELS.map((label) => {
              const n = data.mastery_distribution[label];
              return n ? (
                <div key={label} className={MASTERY_BG[label]} style={{ width: `${(n / total) * 100}%` }} title={`${label}: ${n}`} />
              ) : null;
            })}
          </div>
          <ul className="mt-2 flex flex-wrap gap-x-4 gap-y-1 text-xs text-slate-600" data-testid="mastery-distribution">
            {MASTERY_LABELS.map((label) => (
              <li key={label} className="flex items-center gap-1.5 capitalize">
                <span aria-hidden className={cn("size-2.5 rounded-full", MASTERY_BG[label])} />
                {label} {data.mastery_distribution[label]}
              </li>
            ))}
          </ul>
        </div>
      )}

      <div>
        <h3 className="mb-2 text-sm font-semibold text-slate-900">By subject</h3>
        {data.subjects.length === 0 ? (
          <p className="text-sm text-slate-500">Upload material to see your subjects here.</p>
        ) : (
          <ul className="space-y-3" data-testid="subject-mastery">
            {data.subjects.map((subject) => (
              <li key={subject.id ?? "unsorted"}>
                <div className="flex items-baseline justify-between gap-3 text-sm">
                  {subject.id ? (
                    <Link href={`/concepts?subject_id=${subject.id}`} className="font-medium text-slate-800 hover:underline">
                      {subject.name}
                    </Link>
                  ) : (
                    <span className="font-medium text-slate-800">{subject.name}</span>
                  )}
                  <span className="text-xs text-slate-500">
                    {subject.mastered_count}/{subject.concept_count} mastered
                    {subject.struggling_count > 0 && ` · ${subject.struggling_count} struggling`} ·{" "}
                    {formatPercent(subject.avg_mastery)}
                  </span>
                </div>
                <div
                  role="progressbar"
                  aria-label={`Average mastery in ${subject.name}`}
                  aria-valuemin={0}
                  aria-valuemax={100}
                  aria-valuenow={Math.round(subject.avg_mastery * 100)}
                  className="mt-1 h-2 overflow-hidden rounded-full bg-slate-100"
                >
                  <div
                    className={cn("h-full rounded-full", masteryBg(subject.avg_mastery))}
                    style={{ width: `${Math.max(subject.concept_count ? 2 : 0, Math.round(subject.avg_mastery * 100))}%` }}
                  />
                </div>
              </li>
            ))}
          </ul>
        )}
      </div>
    </Card>
  );
}
