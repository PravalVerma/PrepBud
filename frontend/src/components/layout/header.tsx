"use client";

import { ThemeToggle } from "@/components/layout/theme-toggle";
import { Button } from "@/components/ui/button";
import { Skeleton } from "@/components/ui/feedback";
import { useAuth } from "@/hooks/use-auth";
import { initials } from "@/lib/utils";
import { useUIStore } from "@/stores/ui-store";

export function Header({ title }: { title: string }) {
  const { email, displayName, isLoading, isSigningOut, signOut } = useAuth();
  const mobileNavOpen = useUIStore((s) => s.mobileNavOpen);
  const toggleMobileNav = useUIStore((s) => s.toggleMobileNav);

  return (
    <header className="flex h-16 items-center gap-3 border-b border-slate-200 bg-white px-4 md:px-8">
      <button
        type="button"
        className="rounded-lg p-2 text-slate-600 hover:bg-slate-100 lg:hidden"
        aria-label="Toggle navigation"
        aria-controls="app-sidebar"
        aria-expanded={mobileNavOpen}
        onClick={toggleMobileNav}
      >
        ☰
      </button>
      <h1 className="truncate text-lg font-semibold text-slate-900">{title}</h1>
      <div className="ml-auto flex items-center gap-3">
        <ThemeToggle />
        {isLoading ? (
          <Skeleton className="h-8 w-40" />
        ) : (
          <div className="hidden items-center gap-2 lg:flex" data-testid="current-user">
            <span
              aria-hidden
              className="grid size-8 place-items-center rounded-full bg-brand-100 text-xs font-semibold text-brand-700"
            >
              {initials(displayName ?? email)}
            </span>
            <span className="text-sm text-slate-700">{displayName ?? email}</span>
          </div>
        )}
        <Button variant="secondary" onClick={signOut} loading={isSigningOut}>
          Sign out
        </Button>
      </div>
    </header>
  );
}
