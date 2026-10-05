import type { Metadata } from "next";

import { ActivityChart } from "@/components/dashboard/activity-chart";
import { MasteryOverviewCard } from "@/components/dashboard/mastery-overview-card";
import { AIUsageCard } from "@/components/progress/ai-usage-card";
import { MasteryHeatmap } from "@/components/progress/mastery-heatmap";
import { MisconceptionList } from "@/components/progress/misconception-list";
import { SessionHistory } from "@/components/session/session-history";

export const metadata: Metadata = { title: "Progress" };

export default function ProgressPage() {
  return (
    <div className="space-y-6" data-testid="progress-page">
      <div className="grid gap-6 lg:grid-cols-3">
        <MasteryOverviewCard />
        <AIUsageCard />
      </div>
      <ActivityChart days={90} />
      <MasteryHeatmap />
      <div className="grid gap-6 lg:grid-cols-2">
        <MisconceptionList />
        <SessionHistory />
      </div>
    </div>
  );
}
