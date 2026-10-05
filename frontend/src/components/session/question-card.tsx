"use client";

import { type FormEvent, useState } from "react";

import { Markdown } from "@/components/session/markdown";
import { Button } from "@/components/ui/button";
import { Badge } from "@/components/ui/feedback";
import { cn } from "@/lib/utils";
import type { SessionQuestion } from "@/types/session";

const TYPE_LABEL: Record<string, string> = {
  mcq: "Multiple choice",
  true_false: "True or false",
  short_answer: "Short answer",
  worked_problem: "Worked problem",
  open_ended: "Open question",
};

function difficultyLabel(d: number): string {
  return d < 0.35 ? "Easier" : d < 0.65 ? "Medium" : "Harder";
}

export function QuestionCard({
  question,
  answer,
  active,
  disabled,
  onSubmit,
  onHint,
}: {
  question: SessionQuestion;
  /** What the student answered (the card is then read-only). */
  answer: string | null;
  /** The open question — only it accepts input. */
  active: boolean;
  disabled: boolean;
  onSubmit: (content: string) => void;
  onHint: () => void;
}) {
  const [choice, setChoice] = useState<string | null>(null);
  const [text, setText] = useState("");
  const locked = !active || answer !== null;
  const choices =
    question.type === "mcq"
      ? (question.options ?? []).map((o) => ({ value: o.label, label: o.label, text: o.text }))
      : question.type === "true_false"
        ? [
            { value: "true", label: "", text: "True" },
            { value: "false", label: "", text: "False" },
          ]
        : null;
  const value = choices ? choice : text.trim();

  function submit(event?: FormEvent) {
    event?.preventDefault();
    if (!value || locked || disabled) return;
    onSubmit(value);
  }

  const answered = choices?.find((c) => c.value === answer);
  return (
    <form
      onSubmit={submit}
      data-testid="question-card"
      data-question-type={question.type}
      className="rounded-xl bg-white p-4 shadow-sm ring-1 ring-slate-200"
    >
      <div className="mb-2 flex flex-wrap items-center gap-2">
        <Badge className="bg-brand-50 text-brand-700">{TYPE_LABEL[question.type] ?? "Question"}</Badge>
        <Badge>{difficultyLabel(question.difficulty)}</Badge>
        {question.mode === "review" && <Badge className="bg-amber-50 text-amber-700">Review</Badge>}
      </div>
      <Markdown className="text-base">{question.content}</Markdown>

      {choices ? (
        <div role="radiogroup" aria-label="Answer options" className="mt-3 grid gap-2">
          {choices.map((c) => {
            const selected = (locked ? answer : choice) === c.value;
            return (
              <button
                key={c.value}
                type="button"
                role="radio"
                aria-checked={selected}
                disabled={locked || disabled}
                onClick={() => setChoice(c.value)}
                data-testid="answer-option"
                className={cn(
                  "flex items-start gap-3 rounded-lg px-3 py-2 text-left text-sm ring-1 ring-inset transition-colors",
                  selected ? "bg-brand-50 ring-brand-600" : "ring-slate-200 hover:bg-slate-50",
                  "disabled:cursor-default disabled:hover:bg-transparent",
                  selected && "disabled:hover:bg-brand-50",
                )}
              >
                {c.label && <span className="font-semibold text-slate-500">{c.label}</span>}
                <Markdown className="min-w-0 flex-1">{c.text}</Markdown>
              </button>
            );
          })}
        </div>
      ) : locked ? null : (
        <textarea
          aria-label="Your answer"
          value={text}
          disabled={disabled}
          onChange={(e) => setText(e.target.value)}
          onKeyDown={(e) => {
            if (e.key === "Enter" && (e.ctrlKey || e.metaKey)) submit();
          }}
          rows={question.type === "short_answer" ? 2 : 5}
          maxLength={4000}
          placeholder={question.type === "worked_problem" ? "Show your working and the final answer…" : "Type your answer…"}
          className="mt-3 block w-full rounded-lg border-0 px-3 py-2 text-sm ring-1 ring-inset ring-slate-300 focus:ring-2 focus:ring-brand-600"
        />
      )}

      {answer !== null && !choices && (
        <p className="mt-3 rounded-lg bg-slate-50 px-3 py-2 text-sm text-slate-700" data-testid="submitted-answer">
          <span className="font-medium">Your answer:</span> {answer}
        </p>
      )}
      {answer !== null && answered && <p className="sr-only">You answered {answered.text}</p>}

      {!locked && (
        <div className="mt-3 flex flex-wrap items-center justify-between gap-2">
          <Button
            variant="ghost"
            onClick={onHint}
            disabled={disabled || question.hints_available <= 0}
            title={question.hints_available <= 0 ? "No hints left for this question" : undefined}
          >
            💡 Hint{question.hints_available > 0 ? ` (${question.hints_available} left)` : ""}
          </Button>
          <Button type="submit" disabled={!value || disabled}>
            Submit answer
          </Button>
        </div>
      )}
    </form>
  );
}
