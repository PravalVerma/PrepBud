"use client";

import { useQuery } from "@tanstack/react-query";
import Link from "next/link";
import { useRouter } from "next/navigation";
import { type FormEvent, useState } from "react";

import { Button } from "@/components/ui/button";
import { Card, CardHeader } from "@/components/ui/card";
import { Alert } from "@/components/ui/feedback";
import { SelectField } from "@/components/ui/field";
import { useCreateSession } from "@/hooks/use-sessions";
import { useGoals } from "@/hooks/use-study-plan";
import { ApiError, api } from "@/lib/api";
import type { SessionType } from "@/types/session";

const TYPES: { value: SessionType; label: string; hint: string }[] = [
  { value: "mixed", label: "Mixed (recommended)", hint: "New concepts plus a review of what is due." },
  { value: "teach", label: "Learn something new", hint: "Explanations first, then a quick check." },
  { value: "practice", label: "Practice", hint: "Questions on concepts you have started." },
  { value: "review", label: "Review", hint: "Spaced-repetition review of concepts that are due." },
];

const BUDGETS = [10, 20, 30, 45, 60].map((m) => ({ value: String(m), label: `${m} minutes` }));

/**
 * `conceptId` (from "Study this concept") focuses the session on one concept; `goalId`
 * (from a goal's "Study now") preselects the goal whose concepts the tutor works on.
 */
export function StartSessionForm({ conceptId, goalId }: { conceptId?: string; goalId?: string }) {
  const goals = useGoals({ per_page: 50, status: "active" });
  const [goal, setGoal] = useState(goalId ?? "");
  const concept = useQuery({
    queryKey: ["concepts", "detail", conceptId],
    queryFn: () => api.getConcept(conceptId!),
    enabled: Boolean(conceptId),
  });
  const conceptName = concept.data?.name;
  const router = useRouter();
  const create = useCreateSession();
  const [type, setType] = useState<SessionType>(conceptId ? "teach" : "mixed");
  const [budget, setBudget] = useState("20");

  function submit(event: FormEvent) {
    event.preventDefault();
    create.mutate(
      {
        session_type: type,
        time_budget_minutes: Number(budget),
        ...(conceptId ? { concept_ids: [conceptId] } : {}),
        ...(goal && !conceptId ? { learning_goal_id: goal } : {}),
      },
      { onSuccess: (created) => router.push(`/session/${created.session_id}`) },
    );
  }

  const error = create.error;
  const noConcepts =
    error instanceof ApiError && error.status === 422 && error.details.reason === "NO_CONCEPTS";
  return (
    <Card>
      <CardHeader
        title="Start a study session"
        description={
          conceptName
            ? `Focused on ${conceptName}.`
            : "Your tutor picks concepts from your material based on what you know."
        }
      />
      <form onSubmit={submit} className="grid gap-4 sm:grid-cols-2">
        <SelectField
          label="Session type"
          value={type}
          onChange={(e) => setType(e.target.value as SessionType)}
          options={TYPES.map(({ value, label }) => ({ value, label }))}
          hint={TYPES.find((t) => t.value === type)?.hint}
        />
        <SelectField
          label="Time budget"
          value={budget}
          onChange={(e) => setBudget(e.target.value)}
          options={BUDGETS}
          hint="The session wraps up when time is up."
        />
        {!conceptId && (goals.data?.data.length ?? 0) > 0 && (
          <SelectField
            label="Goal"
            value={goal}
            onChange={(e) => setGoal(e.target.value)}
            options={[
              { value: "", label: "No particular goal" },
              ...(goals.data?.data ?? []).map((g) => ({ value: g.id, label: g.title })),
            ]}
            hint="Concepts from this goal come first."
          />
        )}
        <div className="sm:col-span-2">
          {error && (
            <div className="mb-3">
              <Alert tone="error">
                {noConcepts ? (
                  <>
                    There is nothing to study yet.{" "}
                    <Link href="/upload" className="font-medium underline">
                      Upload some material
                    </Link>{" "}
                    first.
                  </>
                ) : error instanceof ApiError && error.status === 429 ? (
                  "You've started a lot of sessions recently — try again a little later."
                ) : (
                  error.message
                )}
              </Alert>
            </div>
          )}
          <Button type="submit" loading={create.isPending}>
            Start session
          </Button>
        </div>
      </form>
    </Card>
  );
}
