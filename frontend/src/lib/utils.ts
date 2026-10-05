import type { MasteryLabel } from "@/types/domain";

/** Join class names, skipping falsy values. */
export function cn(...classes: (string | false | null | undefined)[]): string {
  return classes.filter(Boolean).join(" ");
}

/** Mastery label for a level in [0, 1] (DOMAIN_MODEL §5 / LEARNING_ENGINE §4.3). */
export function masteryLabel(level: number): MasteryLabel {
  if (level < 0.2) return "novice";
  if (level < 0.4) return "beginner";
  if (level < 0.6) return "intermediate";
  if (level < 0.8) return "proficient";
  return "mastered";
}

export function formatPercent(value: number): string {
  return `${Math.round(value * 100)}%`;
}

export function initials(nameOrEmail: string | null | undefined): string {
  if (!nameOrEmail) return "?";
  const base = nameOrEmail.includes("@") ? nameOrEmail.split("@")[0] : nameOrEmail;
  const parts = base.split(/[\s._-]+/).filter(Boolean);
  return ((parts[0]?.[0] ?? "") + (parts[1]?.[0] ?? "")).toUpperCase() || "?";
}

export function formatDate(iso: string | null | undefined): string {
  if (!iso) return "—";
  return new Date(iso).toLocaleDateString(undefined, { day: "numeric", month: "short", year: "numeric" });
}

/** Calendar-day helpers for date-only API values ("YYYY-MM-DD"), in the viewer's timezone. */
export function isoDay(date: Date): string {
  const pad = (n: number) => String(n).padStart(2, "0");
  return `${date.getFullYear()}-${pad(date.getMonth() + 1)}-${pad(date.getDate())}`;
}

export function parseDay(day: string): Date {
  const [y, m, d] = day.split("-").map(Number);
  return new Date(y, m - 1, d);
}

export function addDays(day: string, days: number): string {
  const date = parseDay(day);
  date.setDate(date.getDate() + days);
  return isoDay(date);
}

/** "Today", "Tomorrow", "Yesterday", or e.g. "Thu 8 Oct". */
export function dayLabel(day: string, today: string = isoDay(new Date())): string {
  if (day === today) return "Today";
  if (day === addDays(today, 1)) return "Tomorrow";
  if (day === addDays(today, -1)) return "Yesterday";
  return parseDay(day).toLocaleDateString(undefined, { weekday: "short", day: "numeric", month: "short" });
}
