import type { Metadata } from "next";

import { ActivityChart } from "@/components/dashboard/activity-chart";
import { MasteryOverviewCard } from "@/components/dashboard/mastery-overview-card";
import { MisconceptionsCard } from "@/components/dashboard/misconceptions-card";
import { OnboardingChecklist } from "@/components/dashboard/onboarding-checklist";
import { RecentSessionsCard } from "@/components/dashboard/recent-sessions-card";
import { SubjectsCard } from "@/components/dashboard/subjects-card";
import { WelcomeBanner } from "@/components/dashboard/welcome-banner";
import { ReviewQueue } from "@/components/review/review-queue";

export const metadata: Metadata = { title: "Dashboard" };

export default function DashboardPage() {
  return (
    <div className="space-y-6" data-testid="dashboard">
      <WelcomeBanner />
      <OnboardingChecklist />
      <div className="grid gap-6 lg:grid-cols-3">
        <MasteryOverviewCard />
        <ReviewQueue compact />
      </div>
      <div className="grid gap-6 md:grid-cols-2 lg:grid-cols-3">
        <ActivityChart />
        <RecentSessionsCard />
        <MisconceptionsCard />
      </div>
      <SubjectsCard />
    </div>
  );
}
