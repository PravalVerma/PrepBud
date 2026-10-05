"use client";

import Link from "next/link";
import { useEffect } from "react";

import { Button } from "@/components/ui/button";
import { Card } from "@/components/ui/card";
import { Skeleton } from "@/components/ui/feedback";

/** Shown by each section's `loading.tsx` while its page streams in. */
export function PageSkeleton() {
  return (
    <div className="space-y-6" aria-busy="true" aria-label="Loading">
      <Skeleton className="h-24 w-full rounded-xl" />
      <div className="grid gap-6 lg:grid-cols-3">
        <Skeleton className="h-48 w-full rounded-xl lg:col-span-2" />
        <Skeleton className="h-48 w-full rounded-xl" />
      </div>
      <Skeleton className="h-32 w-full rounded-xl" />
    </div>
  );
}

/** Error boundary content for a section: the rest of the app (sidebar, header) keeps working. */
export function RouteError({ error, reset }: { error: Error & { digest?: string }; reset: () => void }) {
  useEffect(() => {
    console.error(error);
  }, [error]);
  return (
    <Card role="alert" data-testid="route-error" className="mx-auto max-w-lg text-center">
      <h2 className="text-lg font-semibold text-slate-900">This page hit a problem</h2>
      <p className="mt-2 text-sm text-slate-600">
        Your progress is safe. Try again, or head back to the dashboard.
        {error.digest && <span className="mt-1 block text-xs text-slate-500">Reference: {error.digest}</span>}
      </p>
      <div className="mt-5 flex justify-center gap-2">
        <Button onClick={reset}>Try again</Button>
        <Link
          href="/dashboard"
          className="rounded-lg px-4 py-2 text-sm font-medium text-slate-700 ring-1 ring-inset ring-slate-300 hover:bg-slate-50"
        >
          Dashboard
        </Link>
      </div>
    </Card>
  );
}
