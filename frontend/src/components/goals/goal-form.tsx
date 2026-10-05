"use client";

import { type FormEvent, useState } from "react";

import { Button } from "@/components/ui/button";
import { Alert } from "@/components/ui/feedback";
import { SelectField, TextField } from "@/components/ui/field";
import { useConcepts } from "@/hooks/use-concepts";
import { useDebounced } from "@/hooks/use-debounced";
import { useCreateGoal, useUpdateGoal } from "@/hooks/use-study-plan";
import { useSubjects } from "@/hooks/use-subjects";
import { ApiError } from "@/lib/api";
import { formatPercent, isoDay } from "@/lib/utils";
import type { Goal, GoalCreate, GoalType } from "@/types/study";

type Scope = "concepts" | "subject" | "all";

const TYPES: { value: GoalType; label: string; hint: string }[] = [
  { value: "mastery", label: "Master it", hint: "Work through the concepts at your own pace." },
  { value: "deadline", label: "By a date", hint: "Spread the concepts so you finish by the target date." },
  { value: "exploration", label: "Explore", hint: "A lighter goal — try things out, no pressure." },
];

function initialScope(goal?: Goal): Scope {
  if (!goal) return "concepts";
  if (goal.target_concept_ids.length > 0) return "concepts";
  return goal.subject_id ? "subject" : "all";
}

function ConceptPicker({
  selected,
  onChange,
}: {
  selected: string[];
  onChange: (ids: string[]) => void;
}) {
  const [search, setSearch] = useState("");
  const debounced = useDebounced(search, 250);
  const { data, isLoading } = useConcepts({ per_page: 100, search: debounced || undefined, sort: "name" });
  const concepts = data?.data ?? [];
  const toggle = (id: string) =>
    onChange(selected.includes(id) ? selected.filter((c) => c !== id) : [...selected, id]);
  return (
    <fieldset className="space-y-2">
      <legend className="text-sm font-medium text-slate-700">
        Concepts <span className="font-normal text-slate-500">({selected.length} selected)</span>
      </legend>
      <input
        aria-label="Find concepts"
        value={search}
        onChange={(e) => setSearch(e.target.value)}
        placeholder="Search your concepts…"
        className="block w-full rounded-lg border-0 px-3 py-2 text-sm ring-1 ring-inset ring-slate-300 focus:ring-2 focus:ring-brand-600"
      />
      <div className="max-h-60 overflow-y-auto rounded-lg ring-1 ring-inset ring-slate-200">
        {isLoading ? (
          <p className="p-3 text-sm text-slate-500">Loading…</p>
        ) : concepts.length === 0 ? (
          <p className="p-3 text-sm text-slate-500">
            {debounced ? "No matching concepts." : "No concepts yet — upload some material first."}
          </p>
        ) : (
          <ul className="divide-y divide-slate-100">
            {concepts.map((c) => (
              <li key={c.id}>
                <label className="flex cursor-pointer items-center justify-between gap-3 px-3 py-2 text-sm hover:bg-slate-50">
                  <span className="flex items-center gap-2">
                    <input
                      type="checkbox"
                      checked={selected.includes(c.id)}
                      onChange={() => toggle(c.id)}
                      className="size-4 rounded border-slate-300 text-brand-600"
                    />
                    {c.name}
                  </span>
                  <span className="text-xs text-slate-500">{formatPercent(c.mastery.level)}</span>
                </label>
              </li>
            ))}
          </ul>
        )}
      </div>
    </fieldset>
  );
}

/** Create a goal, or edit one when `goal` is given. */
export function GoalForm({ goal, onDone }: { goal?: Goal; onDone: (goal: Goal) => void }) {
  const create = useCreateGoal();
  const update = useUpdateGoal();
  const subjects = useSubjects({ page: 1, per_page: 100 });
  const [title, setTitle] = useState(goal?.title ?? "");
  const [description, setDescription] = useState(goal?.description ?? "");
  const [type, setType] = useState<GoalType>(goal?.goal_type ?? "mastery");
  const [targetDate, setTargetDate] = useState(goal?.target_date ?? "");
  const [scope, setScope] = useState<Scope>(initialScope(goal));
  const [subjectId, setSubjectId] = useState(goal?.subject_id ?? "");
  const [conceptIds, setConceptIds] = useState<string[]>(goal?.target_concept_ids ?? []);
  const mutation = goal ? update : create;
  const error = mutation.error;
  const fieldErrors = error instanceof ApiError ? error.fieldErrors : {};
  const subjectOptions = (subjects.data?.data ?? []).map((s) => ({ value: s.id, label: s.name }));

  const invalid =
    !title.trim() ||
    (type === "deadline" && !targetDate) ||
    (scope === "concepts" && conceptIds.length === 0) ||
    (scope === "subject" && !subjectId);

  function submit(event: FormEvent) {
    event.preventDefault();
    if (invalid) return;
    const body: GoalCreate = {
      title: title.trim(),
      description: description.trim() || null,
      goal_type: type,
      target_date: targetDate || null,
      subject_id: scope === "subject" ? subjectId : null,
      course_id: null,
      target_concept_ids: scope === "concepts" ? conceptIds : [],
    };
    const done = { onSuccess: (saved: unknown) => onDone(saved as Goal) };
    if (goal) update.mutate({ id: goal.id, body }, done);
    else create.mutate(body, done);
  }

  return (
    <form onSubmit={submit} className="space-y-4" data-testid="goal-form">
      <TextField
        label="Goal"
        value={title}
        onChange={(e) => setTitle(e.target.value)}
        placeholder="e.g. Master differentiation"
        maxLength={200}
        error={fieldErrors.title}
        required
      />
      <div className="space-y-1.5">
        <label htmlFor="goal-description" className="block text-sm font-medium text-slate-700">
          Notes <span className="font-normal text-slate-500">(optional)</span>
        </label>
        <textarea
          id="goal-description"
          value={description}
          onChange={(e) => setDescription(e.target.value)}
          rows={2}
          maxLength={2000}
          className="block w-full rounded-lg border-0 px-3 py-2 text-sm ring-1 ring-inset ring-slate-300 focus:ring-2 focus:ring-brand-600"
        />
      </div>
      <div className="grid gap-4 sm:grid-cols-2">
        <SelectField
          label="Kind of goal"
          value={type}
          onChange={(e) => setType(e.target.value as GoalType)}
          options={TYPES.map(({ value, label }) => ({ value, label }))}
          hint={TYPES.find((t) => t.value === type)?.hint}
        />
        <TextField
          label={type === "deadline" ? "Target date" : "Target date (optional)"}
          type="date"
          value={targetDate}
          min={isoDay(new Date())}
          onChange={(e) => setTargetDate(e.target.value)}
          error={fieldErrors.target_date}
          required={type === "deadline"}
        />
      </div>
      <SelectField
        label="What to study"
        value={scope}
        onChange={(e) => setScope(e.target.value as Scope)}
        options={[
          { value: "concepts", label: "Specific concepts" },
          { value: "subject", label: "A whole subject" },
          { value: "all", label: "Everything I've uploaded" },
        ]}
      />
      {scope === "subject" && (
        <SelectField
          label="Subject"
          value={subjectId}
          onChange={(e) => setSubjectId(e.target.value)}
          options={[{ value: "", label: "Choose a subject…" }, ...subjectOptions]}
        />
      )}
      {scope === "concepts" && <ConceptPicker selected={conceptIds} onChange={setConceptIds} />}
      {error && <Alert tone="error">{error.message}</Alert>}
      <Button type="submit" loading={mutation.isPending} disabled={invalid}>
        {goal ? "Save goal" : "Create goal & plan"}
      </Button>
    </form>
  );
}
