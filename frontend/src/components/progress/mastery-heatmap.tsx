"use client";

import Link from "next/link";
import { useState } from "react";

import { Card, CardHeader } from "@/components/ui/card";
import { Alert, Skeleton } from "@/components/ui/feedback";
import { SelectField } from "@/components/ui/field";
import { useMasteryHeatmap } from "@/hooks/use-dashboard";
import { useSubjects } from "@/hooks/use-subjects";
import { MASTERY_BG, MASTERY_LABELS, masteryBg } from "@/lib/mastery";
import { cn, formatPercent, parseDay } from "@/lib/utils";

const WEEK_OPTIONS = [4, 8, 12, 26].map((w) => ({ value: String(w), label: `Last ${w} weeks` }));

function columnLabel(day: string, last: boolean): string {
  return last ? "Now" : parseDay(day).toLocaleDateString(undefined, { day: "numeric", month: "short" });
}

/** Concepts × weeks, coloured by mastery at the end of each week. */
export function MasteryHeatmap() {
  const [subjectId, setSubjectId] = useState("");
  const [weeks, setWeeks] = useState("8");
  const subjects = useSubjects({ page: 1, per_page: 100 });
  const { data, isLoading, error } = useMasteryHeatmap({
    subject_id: subjectId || undefined,
    weeks: Number(weeks),
    limit: 100,
  });

  return (
    <Card data-testid="mastery-heatmap">
      <CardHeader title="Mastery over time" description="Each square is a concept's mastery at the end of that week." />
      <div className="mb-4 grid gap-3 sm:grid-cols-2">
        <SelectField
          label="Subject"
          value={subjectId}
          onChange={(e) => setSubjectId(e.target.value)}
          options={[
            { value: "", label: "All subjects" },
            ...(subjects.data?.data ?? []).map((s) => ({ value: s.id, label: s.name })),
          ]}
        />
        <SelectField label="Period" value={weeks} onChange={(e) => setWeeks(e.target.value)} options={WEEK_OPTIONS} />
      </div>
      {isLoading ? (
        <Skeleton className="h-48 w-full" />
      ) : error || !data ? (
        <Alert tone="error">The heatmap is unavailable right now.</Alert>
      ) : data.concepts.length === 0 ? (
        <p className="text-sm text-slate-500">No concepts yet.</p>
      ) : (
        <>
          <div className="overflow-x-auto">
            <table className="w-full border-separate border-spacing-1 text-xs">
              <caption className="sr-only">Mastery per concept per week</caption>
              <thead>
                <tr>
                  <th scope="col" className="sticky left-0 bg-white text-left font-medium text-slate-500">
                    Concept
                  </th>
                  {data.columns.map((day, i) => (
                    <th key={day} scope="col" className="min-w-8 whitespace-nowrap px-0.5 text-center font-normal text-slate-500">
                      {columnLabel(day, i === data.columns.length - 1)}
                    </th>
                  ))}
                </tr>
              </thead>
              <tbody>
                {data.concepts.map((concept) => (
                  <tr key={concept.id} data-testid="heatmap-row">
                    <th scope="row" className="sticky left-0 max-w-48 truncate bg-white pr-2 text-left font-medium">
                      <Link href={`/concepts/${concept.id}`} className="text-slate-800 hover:underline" title={concept.name}>
                        {concept.name}
                      </Link>
                    </th>
                    {concept.cells.map((value, i) => (
                      <td key={data.columns[i]} className="p-0">
                        <div
                          data-testid="heatmap-cell"
                          title={`${concept.name}, ${columnLabel(data.columns[i], i === concept.cells.length - 1)}: ${
                            value === null ? "not studied yet" : formatPercent(value)
                          }`}
                          className={cn(
                            "mx-auto h-6 w-full min-w-6 rounded",
                            value === null ? "bg-slate-100" : masteryBg(value),
                          )}
                        >
                          <span className="sr-only">{value === null ? "not studied" : formatPercent(value)}</span>
                        </div>
                      </td>
                    ))}
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
          <div className="mt-3 flex flex-wrap items-center justify-between gap-3 text-xs text-slate-600">
            <ul className="flex flex-wrap gap-x-3 gap-y-1">
              <li className="flex items-center gap-1.5">
                <span aria-hidden className="size-3 rounded bg-slate-100" /> not studied
              </li>
              {MASTERY_LABELS.map((label) => (
                <li key={label} className="flex items-center gap-1.5 capitalize">
                  <span aria-hidden className={cn("size-3 rounded", MASTERY_BG[label])} /> {label}
                </li>
              ))}
            </ul>
            {data.truncated && (
              <span>
                Showing {data.concepts.length} of {data.total_concepts} concepts (most recently studied first)
              </span>
            )}
          </div>
        </>
      )}
    </Card>
  );
}
