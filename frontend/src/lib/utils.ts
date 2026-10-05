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
