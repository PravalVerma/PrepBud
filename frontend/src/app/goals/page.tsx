import type { Metadata } from "next";

import { GoalList } from "@/components/goals/goal-list";
import { NewGoalCard } from "@/components/goals/new-goal-card";

export const metadata: Metadata = { title: "Goals" };

export default function GoalsPage() {
  return (
    <div className="space-y-6" data-testid="goals-page">
      <NewGoalCard />
      <GoalList />
    </div>
  );
}
