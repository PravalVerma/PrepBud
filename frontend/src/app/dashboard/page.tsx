import type { Metadata } from "next";

import { PlaceholderCard } from "@/components/dashboard/placeholder-card";
import { RecentSessionsCard } from "@/components/dashboard/recent-sessions-card";
import { SubjectsCard } from "@/components/dashboard/subjects-card";
import { WelcomeBanner } from "@/components/dashboard/welcome-banner";
import { ReviewQueue } from "@/components/review/review-queue";

export const metadata: Metadata = { title: "Dashboard" };

export default function DashboardPage() {
  return (
    <div className="space-y-6" data-testid="dashboard">
      <WelcomeBanner />
      <div className="grid gap-6 lg:grid-cols-3">
        <PlaceholderCard
          title="Mastery overview"
          description="Your progress across every concept."
          phase={7}
        />
        <ReviewQueue compact />
        <RecentSessionsCard />
      </div>
      <SubjectsCard />
    </div>
  );
}
