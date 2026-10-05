"use client";

import Link from "next/link";
import { useState } from "react";

import { GoalForm } from "@/components/goals/goal-form";
import { Button } from "@/components/ui/button";
import { Card, CardHeader } from "@/components/ui/card";
import { Alert } from "@/components/ui/feedback";
import type { Goal } from "@/types/study";

export function NewGoalCard() {
  const [open, setOpen] = useState(false);
  const [created, setCreated] = useState<Goal | null>(null);
  return (
    <Card>
      <CardHeader
        title="Set a learning goal"
        description="Pick what you want to learn (and by when) — your study plan is built from it."
        action={
          !open && (
            <Button onClick={() => { setOpen(true); setCreated(null); }}>New goal</Button>
          )
        }
      />
      {created && !open && (
        <Alert tone="success">
          “{created.title}” created — your plan has {created.study_plan?.total_items ?? 0} item
          {created.study_plan?.total_items === 1 ? "" : "s"}, {created.study_plan?.due_today ?? 0} for today.{" "}
          <Link href="/review" className="font-medium underline">
            See the plan
          </Link>
        </Alert>
      )}
      {open && (
        <div className="space-y-3">
          <GoalForm
            onDone={(goal) => {
              setCreated(goal);
              setOpen(false);
            }}
          />
          <Button variant="ghost" onClick={() => setOpen(false)}>
            Cancel
          </Button>
        </div>
      )}
    </Card>
  );
}
