"use client";

import { type FormEvent, useState } from "react";

import { Button } from "@/components/ui/button";
import { Card, CardHeader } from "@/components/ui/card";
import { TextField } from "@/components/ui/field";
import { Alert, Badge, Skeleton } from "@/components/ui/feedback";
import { useCreateSubject, useSubjects } from "@/hooks/use-subjects";
import { ApiError } from "@/lib/api";
import { formatPercent, masteryLabel } from "@/lib/utils";

export function SubjectsCard() {
  const { data, isLoading, error } = useSubjects();
  const create = useCreateSubject();
  const [name, setName] = useState("");
  const [showForm, setShowForm] = useState(false);

  const subjects = data?.data ?? [];
  const createError =
    create.error instanceof ApiError
      ? (create.error.fieldErrors.name ?? create.error.message)
      : create.error
        ? "Could not create subject."
        : undefined;

  async function onSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    try {
      await create.mutateAsync({ name });
      setName("");
      setShowForm(false);
    } catch {
      // surfaced via create.error
    }
  }

  return (
    <Card data-testid="subjects-card">
      <CardHeader
        title="Subjects"
        description="Organise your material by subject, course and chapter."
        action={
          <Button variant="secondary" onClick={() => setShowForm((v) => !v)}>
            {showForm ? "Cancel" : "Add subject"}
          </Button>
        }
      />

      {showForm && (
        <form onSubmit={onSubmit} className="mb-4 flex items-end gap-3">
          <div className="flex-1">
            <TextField
              label="Subject name"
              value={name}
              onChange={(e) => setName(e.target.value)}
              maxLength={200}
              placeholder="e.g. Mathematics"
              error={createError}
              required
            />
          </div>
          <Button type="submit" loading={create.isPending} disabled={!name.trim()}>
            Save
          </Button>
        </form>
      )}

      {isLoading ? (
        <div className="space-y-3">
          <Skeleton className="h-12 w-full" />
          <Skeleton className="h-12 w-full" />
        </div>
      ) : error ? (
        <Alert tone="error">Could not load subjects. {error.message}</Alert>
      ) : subjects.length === 0 ? (
        <p className="rounded-lg border border-dashed border-slate-300 p-6 text-center text-sm text-slate-500">
          No subjects yet. Add one to start organising your material.
        </p>
      ) : (
        <ul className="divide-y divide-slate-100">
          {subjects.map((s) => (
            <li key={s.id} className="flex items-center gap-3 py-3">
              <span aria-hidden className="grid size-9 place-items-center rounded-lg bg-slate-100 text-lg">
                {s.icon ?? "📘"}
              </span>
              <div className="min-w-0 flex-1">
                <p className="truncate text-sm font-medium text-slate-900">{s.name}</p>
                <p className="text-xs text-slate-500">
                  {s.course_count} {s.course_count === 1 ? "course" : "courses"} · {s.concept_count}{" "}
                  {s.concept_count === 1 ? "concept" : "concepts"}
                </p>
              </div>
              <Badge>
                {formatPercent(s.avg_mastery)} · {masteryLabel(s.avg_mastery)}
              </Badge>
            </li>
          ))}
        </ul>
      )}
    </Card>
  );
}
