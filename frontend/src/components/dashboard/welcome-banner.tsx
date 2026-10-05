"use client";

import Link from "next/link";

import { Skeleton } from "@/components/ui/feedback";
import { useProfile } from "@/hooks/use-profile";

export function WelcomeBanner() {
  const { data: profile, isLoading } = useProfile();
  if (isLoading) return <Skeleton className="h-24 w-full rounded-xl" />;

  const name = profile?.display_name ?? profile?.email?.split("@")[0] ?? "there";
  const needsProfile = profile?.onboarding_state === "new";

  return (
    <section className="rounded-xl bg-gradient-to-r from-brand-600 to-brand-500 p-6 text-white shadow-sm">
      <h2 className="text-xl font-semibold">Welcome, {name}</h2>
      <p className="mt-1 text-sm text-white/85">
        Upload your study material and your tutor will build a personalised plan around it.
      </p>
      {needsProfile && (
        <Link
          href="/profile"
          className="mt-4 inline-block rounded-lg bg-white/15 px-3 py-1.5 text-sm font-medium hover:bg-white/25"
        >
          Complete your profile →
        </Link>
      )}
    </section>
  );
}
