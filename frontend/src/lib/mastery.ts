/** Mastery colour scale shared by charts, heatmap and concept graph (AC-7.2). */
import type { MasteryLabel } from "@/types/domain";

import { masteryLabel } from "@/lib/utils";

export const MASTERY_LABELS: MasteryLabel[] = ["novice", "beginner", "intermediate", "proficient", "mastered"];

/** Mid shades only (300–600), so the scale reads the same in light and dark mode. */
export const MASTERY_BG: Record<MasteryLabel, string> = {
  novice: "bg-slate-300",
  beginner: "bg-amber-400",
  intermediate: "bg-brand-400",
  proficient: "bg-emerald-400",
  mastered: "bg-emerald-600",
};

export const MASTERY_FILL: Record<MasteryLabel, string> = {
  novice: "fill-slate-300",
  beginner: "fill-amber-400",
  intermediate: "fill-brand-400",
  proficient: "fill-emerald-400",
  mastered: "fill-emerald-600",
};

export const masteryBg = (level: number) => MASTERY_BG[masteryLabel(level)];
export const masteryFill = (level: number) => MASTERY_FILL[masteryLabel(level)];

export function formatMinutes(minutes: number): string {
  if (minutes < 60) return `${Math.round(minutes)} min`;
  const h = Math.floor(minutes / 60);
  const m = Math.round(minutes % 60);
  return m ? `${h} h ${m} min` : `${h} h`;
}
