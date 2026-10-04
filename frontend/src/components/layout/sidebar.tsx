"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";

import { NAV_ITEMS } from "@/components/layout/nav-items";
import { cn } from "@/lib/utils";
import { useUIStore } from "@/stores/ui-store";

export function Sidebar() {
  const pathname = usePathname();
  const mobileNavOpen = useUIStore((s) => s.mobileNavOpen);
  const setMobileNavOpen = useUIStore((s) => s.setMobileNavOpen);

  return (
    <>
      {mobileNavOpen && (
        <div
          aria-hidden
          className="fixed inset-0 z-30 bg-slate-900/40 md:hidden"
          onClick={() => setMobileNavOpen(false)}
        />
      )}
      <aside
        id="app-sidebar"
        className={cn(
          "fixed inset-y-0 left-0 z-40 flex w-64 flex-col border-r border-slate-200 bg-white transition-transform md:static md:translate-x-0",
          mobileNavOpen ? "translate-x-0" : "-translate-x-full",
        )}
      >
        <div className="flex h-16 items-center gap-2 border-b border-slate-200 px-5">
          <span className="grid size-8 place-items-center rounded-lg bg-brand-600 text-sm font-bold text-white">
            SB
          </span>
          <span className="font-semibold text-slate-900">School in a Box</span>
        </div>
        <nav aria-label="Main" className="flex-1 space-y-1 p-3">
          {NAV_ITEMS.map((item) => {
            const active = pathname === item.href || pathname.startsWith(`${item.href}/`);
            if (item.comingInPhase) {
              return (
                <span
                  key={item.href}
                  aria-disabled="true"
                  className="flex cursor-default items-center gap-3 rounded-lg px-3 py-2 text-sm text-slate-400"
                >
                  <span aria-hidden className="w-4 text-center">
                    {item.icon}
                  </span>
                  {item.label}
                  <span className="ml-auto rounded bg-slate-100 px-1.5 text-[10px] uppercase tracking-wide">
                    Soon
                  </span>
                </span>
              );
            }
            return (
              <Link
                key={item.href}
                href={item.href}
                aria-current={active ? "page" : undefined}
                onClick={() => setMobileNavOpen(false)}
                className={cn(
                  "flex items-center gap-3 rounded-lg px-3 py-2 text-sm font-medium transition-colors",
                  active ? "bg-brand-50 text-brand-700" : "text-slate-700 hover:bg-slate-100",
                )}
              >
                <span aria-hidden className="w-4 text-center">
                  {item.icon}
                </span>
                {item.label}
              </Link>
            );
          })}
        </nav>
      </aside>
    </>
  );
}
