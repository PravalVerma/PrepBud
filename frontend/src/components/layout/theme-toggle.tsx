"use client";

import { useEffect, useState } from "react";

import { applyTheme, readTheme, type ThemeChoice } from "@/lib/theme";

const NEXT: Record<ThemeChoice, ThemeChoice> = { system: "light", light: "dark", dark: "system" };
const ICON: Record<ThemeChoice, string> = { system: "◐", light: "☀", dark: "☾" };
const LABEL: Record<ThemeChoice, string> = { system: "System theme", light: "Light theme", dark: "Dark theme" };

/** Cycles system → light → dark; follows the OS while on "system". */
export function ThemeToggle() {
  const [choice, setChoice] = useState<ThemeChoice>("system");

  useEffect(() => {
    // Sync with what the pre-paint script applied (localStorage is client-only).
    // eslint-disable-next-line react-hooks/set-state-in-effect
    setChoice(readTheme());
  }, []);

  useEffect(() => {
    if (choice !== "system" || !window.matchMedia) return;
    const media = window.matchMedia("(prefers-color-scheme: dark)");
    const onChange = () => applyTheme("system");
    media.addEventListener?.("change", onChange);
    return () => media.removeEventListener?.("change", onChange);
  }, [choice]);

  return (
    <button
      type="button"
      data-testid="theme-toggle"
      aria-label={`${LABEL[choice]} (click to change)`}
      title={LABEL[choice]}
      onClick={() => {
        const next = NEXT[choice];
        applyTheme(next);
        setChoice(next);
      }}
      className="grid size-9 place-items-center rounded-lg text-lg text-slate-600 hover:bg-slate-100"
    >
      <span aria-hidden>{ICON[choice]}</span>
    </button>
  );
}
