"use client";

import { useEffect, useState } from "react";

import { cn, formatPercent, masteryLabel } from "@/lib/utils";

/**
 * Mastery of one concept; animates from the previous value when an answer moves it
 * (AC-5.4) and briefly shows the change.
 */
export function MasteryBar({
  name,
  value,
  previous,
  compact = false,
}: {
  name: string;
  value: number;
  previous?: number | null;
  compact?: boolean;
}) {
  const [shown, setShown] = useState(previous ?? value);
  useEffect(() => {
    const frame = requestAnimationFrame(() => setShown(value));
    return () => cancelAnimationFrame(frame);
  }, [value]);

  const delta = previous === null || previous === undefined ? 0 : Math.round((value - previous) * 100);
  return (
    <div data-testid="mastery-bar" className={cn("min-w-0", compact ? "space-y-1" : "space-y-1.5")}>
      <div className="flex items-baseline justify-between gap-3 text-xs">
        <span className="truncate font-medium text-slate-700">{name}</span>
        <span className="flex shrink-0 items-center gap-2 text-slate-500">
          {delta !== 0 && (
            <span
              key={`${previous}-${value}`}
              data-testid="mastery-delta"
              className={cn(
                "animate-pulse rounded-full px-1.5 font-semibold",
                delta > 0 ? "bg-emerald-50 text-emerald-700" : "bg-amber-50 text-amber-700",
              )}
            >
              {delta > 0 ? `+${delta}` : delta}%
            </span>
          )}
          <span data-testid="mastery-value">{formatPercent(value)}</span>
          <span className="capitalize">· {masteryLabel(value)}</span>
        </span>
      </div>
      <div
        role="progressbar"
        aria-label={`Mastery of ${name}`}
        aria-valuemin={0}
        aria-valuemax={100}
        aria-valuenow={Math.round(value * 100)}
        className={cn("overflow-hidden rounded-full bg-slate-100", compact ? "h-1.5" : "h-2.5")}
      >
        <div
          className={cn(
            "h-full rounded-full transition-[width] duration-700 ease-out",
            value >= 0.8 ? "bg-emerald-500" : value >= 0.4 ? "bg-brand-600" : "bg-amber-500",
          )}
          style={{ width: `${Math.max(2, Math.round(shown * 100))}%` }}
        />
      </div>
    </div>
  );
}
