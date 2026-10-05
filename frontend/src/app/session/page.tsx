import type { Metadata } from "next";

import { SessionHistory } from "@/components/session/session-history";
import { StartSessionForm } from "@/components/session/start-session-form";

export const metadata: Metadata = { title: "Study session" };

const UUID = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i;

export default async function SessionsPage({
  searchParams,
}: {
  searchParams: Promise<{ concept_id?: string; goal_id?: string }>;
}) {
  const { concept_id, goal_id } = await searchParams;
  return (
    <div className="space-y-6" data-testid="sessions-page">
      <StartSessionForm
        conceptId={concept_id && UUID.test(concept_id) ? concept_id : undefined}
        goalId={goal_id && UUID.test(goal_id) ? goal_id : undefined}
      />
      <SessionHistory />
    </div>
  );
}
