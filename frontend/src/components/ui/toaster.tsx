"use client";

import { cn } from "@/lib/utils";
import { type ToastTone, useToastStore } from "@/stores/toast-store";

const TONES: Record<ToastTone, string> = {
  success: "bg-emerald-50 text-emerald-800 ring-emerald-200",
  error: "bg-red-50 text-red-800 ring-red-200",
  info: "bg-white text-slate-800 ring-slate-200",
};

const ICONS: Record<ToastTone, string> = { success: "✓", error: "!", info: "i" };

export function Toaster() {
  const toasts = useToastStore((s) => s.toasts);
  const dismiss = useToastStore((s) => s.dismiss);
  return (
    <div
      aria-live="polite"
      className="pointer-events-none fixed inset-x-4 bottom-4 z-50 flex flex-col items-end gap-2 sm:left-auto sm:w-96"
    >
      {toasts.map((t) => (
        <div
          key={t.id}
          role={t.tone === "error" ? "alert" : "status"}
          data-testid="toast"
          data-tone={t.tone}
          className={cn(
            "pointer-events-auto flex w-full items-start gap-3 rounded-lg px-4 py-3 text-sm shadow-lg ring-1 ring-inset",
            TONES[t.tone],
          )}
        >
          <span aria-hidden className="font-bold">
            {ICONS[t.tone]}
          </span>
          <p className="flex-1">{t.message}</p>
          <button
            type="button"
            aria-label="Dismiss notification"
            onClick={() => dismiss(t.id)}
            className="opacity-70 hover:opacity-100"
          >
            ×
          </button>
        </div>
      ))}
    </div>
  );
}
