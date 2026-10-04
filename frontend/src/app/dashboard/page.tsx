import type { Metadata } from "next";

import { PlaceholderCard } from "@/components/dashboard/placeholder-card";
import { SubjectsCard } from "@/components/dashboard/subjects-card";
import { WelcomeBanner } from "@/components/dashboard/welcome-banner";

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
        <PlaceholderCard title="Due for review" description="Spaced-repetition items for today." phase={6} />
        <PlaceholderCard title="Recent sessions" description="Pick up where you left off." phase={5} />
      </div>
      <SubjectsCard />
    </div>
  );
}
