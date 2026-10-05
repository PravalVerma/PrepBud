"use client";

import { useState } from "react";

import { Card, CardHeader } from "@/components/ui/card";
import { Alert, Skeleton } from "@/components/ui/feedback";
import { useAIUsage } from "@/hooks/use-dashboard";
import { cn } from "@/lib/utils";
import type { UsagePeriod } from "@/types/dashboard";

const PERIODS: { value: UsagePeriod; label: string }[] = [
  { value: "today", label: "Today" },
  { value: "week", label: "7 days" },
  { value: "month", label: "30 days" },
];

const PURPOSE_LABEL: Record<string, string> = {
  tutor_explanation: "Explanations",
  generate_question: "Questions",
  evaluate_answer: "Answer checking",
  detect_misconception: "Misconceptions",
  concept_extraction: "Reading material",
  build_relationships: "Concept links",
  document_embedding: "Search index",
  retrieve_content: "Search",
  session_summary: "Summaries",
};

function usd(value: number): string {
  return value < 0.01 && value > 0 ? "<$0.01" : `$${value.toFixed(2)}`;
}

function label(purpose: string): string {
  return PURPOSE_LABEL[purpose] ?? purpose.replace(/_/g, " ").replace(/^\w/, (c) => c.toUpperCase());
}

/** AI cost tracking (API_CONTRACT §3.13): spend vs. the daily budget, by purpose. */
export function AIUsageCard() {
  const [period, setPeriod] = useState<UsagePeriod>("today");
  const { data, isLoading, error } = useAIUsage(period);
  const budgetShare = data && data.daily_budget_usd > 0 ? Math.min(1, data.today_cost_usd / data.daily_budget_usd) : 0;
  return (
    <Card data-testid="ai-usage">
      <CardHeader
        title="AI usage"
        description="What your tutor's AI calls cost, against your daily budget."
        action={
          <div role="tablist" aria-label="Usage period" className="flex rounded-lg bg-slate-100 p-0.5 text-xs">
            {PERIODS.map((p) => (
              <button
                key={p.value}
                type="button"
                role="tab"
                aria-selected={period === p.value}
                onClick={() => setPeriod(p.value)}
                className={cn("rounded-md px-2 py-1", period === p.value ? "bg-white font-medium shadow-sm" : "text-slate-600")}
              >
                {p.label}
              </button>
            ))}
          </div>
        }
      />
      {isLoading ? (
        <Skeleton className="h-28 w-full" />
      ) : error || !data ? (
        <Alert tone="error">Usage is unavailable right now.</Alert>
      ) : (
        <div className="space-y-4">
          <dl className="grid grid-cols-3 gap-3 text-sm">
            <div>
              <dt className="text-xs text-slate-500">Cost</dt>
              <dd className="text-lg font-semibold text-slate-900" data-testid="ai-cost">{usd(data.total_cost_usd)}</dd>
            </div>
            <div>
              <dt className="text-xs text-slate-500">AI calls</dt>
              <dd className="text-lg font-semibold text-slate-900">{data.total_interactions}</dd>
            </div>
            <div>
              <dt className="text-xs text-slate-500">Tokens</dt>
              <dd className="text-lg font-semibold text-slate-900">{data.total_tokens.toLocaleString()}</dd>
            </div>
          </dl>
          {data.daily_budget_usd > 0 && (
            <div>
              <div className="flex justify-between text-xs text-slate-600">
                <span>Today&apos;s budget</span>
                <span>
                  {usd(data.today_cost_usd)} of {usd(data.daily_budget_usd)}
                </span>
              </div>
              <div
                role="progressbar"
                aria-label="Share of today's AI budget used"
                aria-valuemin={0}
                aria-valuemax={100}
                aria-valuenow={Math.round(budgetShare * 100)}
                className="mt-1 h-2 overflow-hidden rounded-full bg-slate-100"
              >
                <div
                  className={cn("h-full rounded-full", budgetShare > 0.8 ? "bg-red-500" : "bg-brand-500")}
                  style={{ width: `${Math.max(budgetShare > 0 ? 2 : 0, budgetShare * 100)}%` }}
                />
              </div>
            </div>
          )}
          {Object.keys(data.by_purpose).length > 0 ? (
            <table className="w-full text-sm">
              <caption className="sr-only">AI usage by purpose</caption>
              <thead>
                <tr className="text-left text-xs text-slate-500">
                  <th scope="col" className="font-normal">What for</th>
                  <th scope="col" className="text-right font-normal">Calls</th>
                  <th scope="col" className="text-right font-normal">Cost</th>
                </tr>
              </thead>
              <tbody className="divide-y divide-slate-100">
                {Object.entries(data.by_purpose).map(([purpose, b]) => (
                  <tr key={purpose}>
                    <td className="py-1.5 text-slate-800">
                      {label(purpose)}
                      {b.failed > 0 && <span className="ml-1 text-xs text-red-600">({b.failed} failed)</span>}
                    </td>
                    <td className="py-1.5 text-right text-slate-600">{b.count}</td>
                    <td className="py-1.5 text-right text-slate-600">{usd(b.cost)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          ) : (
            <p className="text-sm text-slate-500">No AI calls in this period.</p>
          )}
        </div>
      )}
    </Card>
  );
}
