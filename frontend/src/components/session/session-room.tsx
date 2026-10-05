"use client";

import { useQuery } from "@tanstack/react-query";
import Link from "next/link";
import { type FormEvent, type ReactNode, useEffect, useRef, useState } from "react";

import { Markdown } from "@/components/session/markdown";
import { MasteryBar } from "@/components/session/mastery-bar";
import { QuestionCard } from "@/components/session/question-card";
import { SessionSummaryCard } from "@/components/session/session-summary";
import { Button } from "@/components/ui/button";
import { Card } from "@/components/ui/card";
import { Alert, Badge, Skeleton } from "@/components/ui/feedback";
import { useSessionChannel } from "@/hooks/use-session-channel";
import { sessionsKey } from "@/hooks/use-sessions";
import { ApiError, api } from "@/lib/api";
import { cn, formatPercent } from "@/lib/utils";
import { type ConnectionState, type TimelineItem, useSessionStore } from "@/stores/session-store";
import type { Evaluation } from "@/types/session";

const EXPLAIN_LABEL: Record<string, string> = {
  intro: "Explanation",
  retry: "Another way to see it",
  worked_example: "Worked example",
  socratic: "Let's think it through",
  followup: "Answer",
  resume: "Where we left off",
};

const CONNECTION: Record<ConnectionState, { label: string; tone: string }> = {
  idle: { label: "Starting…", tone: "bg-slate-100 text-slate-600" },
  connecting: { label: "Connecting…", tone: "bg-slate-100 text-slate-600" },
  open: { label: "Live", tone: "bg-emerald-50 text-emerald-700" },
  reconnecting: { label: "Reconnecting…", tone: "bg-amber-50 text-amber-700" },
  closed: { label: "Closed", tone: "bg-slate-100 text-slate-600" },
  failed: { label: "Offline", tone: "bg-red-50 text-red-700" },
};

function EvaluationCard({ evaluation, conceptName }: { evaluation: Evaluation; conceptName: string }) {
  const update = evaluation.mastery_update;
  const partial = !evaluation.is_correct && evaluation.score > 0;
  return (
    <div
      data-testid="evaluation"
      data-correct={evaluation.is_correct}
      className={cn(
        "rounded-xl p-4 ring-1 ring-inset",
        evaluation.is_correct ? "bg-emerald-50 ring-emerald-200" : "bg-amber-50 ring-amber-200",
      )}
    >
      <p className={cn("font-semibold", evaluation.is_correct ? "text-emerald-800" : "text-amber-800")}>
        {evaluation.is_correct ? "✓ Correct" : partial ? `◐ Partly right (${formatPercent(evaluation.score)})` : "✗ Not quite"}
      </p>
      {evaluation.explanation && <Markdown className="mt-1">{evaluation.explanation}</Markdown>}
      {evaluation.correct_answer && (
        <div className="mt-2 text-sm text-slate-700">
          <span className="font-medium">Correct answer: </span>
          <Markdown className="inline-block align-top">{evaluation.correct_answer}</Markdown>
        </div>
      )}
      {update && (
        <div className="mt-3 max-w-sm">
          <MasteryBar name={conceptName} value={update.new_mastery} previous={update.old_mastery} compact />
        </div>
      )}
    </div>
  );
}

function TutorBubble({ children, label }: { children: ReactNode; label?: string }) {
  return (
    <div className="max-w-3xl rounded-xl bg-white p-4 shadow-sm ring-1 ring-slate-200">
      {label && <p className="mb-1 text-xs font-medium uppercase tracking-wide text-brand-700">{label}</p>}
      {children}
    </div>
  );
}

function Item({
  item,
  activeQuestionId,
  busy,
  conceptNames,
  onAnswer,
  onHint,
}: {
  item: TimelineItem;
  activeQuestionId: string | null;
  busy: boolean;
  conceptNames: Record<string, string>;
  onAnswer: (questionId: string, content: string) => void;
  onHint: () => void;
}) {
  switch (item.kind) {
    case "explanation":
      return (
        <div data-testid="explanation" data-streaming={item.streaming}>
          <TutorBubble label={`${EXPLAIN_LABEL[item.explainKind] ?? "Explanation"}${item.conceptName ? ` · ${item.conceptName}` : ""}`}>
            <Markdown>{item.content}</Markdown>
            {item.streaming && (
              <span aria-hidden className="ml-0.5 inline-block h-4 w-1.5 animate-pulse bg-brand-600 align-middle" />
            )}
          </TutorBubble>
        </div>
      );
    case "question":
      return (
        <QuestionCard
          question={item.question}
          answer={item.answer}
          active={item.question.question_id === activeQuestionId}
          disabled={busy}
          onSubmit={(content) => onAnswer(item.question.question_id, content)}
          onHint={onHint}
        />
      );
    case "student":
      return (
        <div className="flex justify-end">
          <p data-testid="student-message" className="max-w-xl rounded-xl bg-brand-600 px-4 py-2 text-sm text-white">
            {item.text}
          </p>
        </div>
      );
    case "evaluation":
      return (
        <EvaluationCard
          evaluation={item.evaluation}
          conceptName={conceptNames[item.evaluation.mastery_update?.concept_id ?? ""] ?? "This concept"}
        />
      );
    case "hint":
      return (
        <div data-testid="hint" className="max-w-3xl rounded-xl bg-amber-50 p-3 text-sm ring-1 ring-amber-200">
          <p className="text-xs font-medium text-amber-800">Hint {item.hintNumber}</p>
          <Markdown>{item.content}</Markdown>
        </div>
      );
    case "misconception":
      return (
        <div data-testid="misconception" className="max-w-3xl rounded-xl bg-violet-50 p-3 ring-1 ring-violet-200">
          <p className="text-xs font-medium text-violet-800">Common mix-up: {item.name.replace(/_/g, " ")}</p>
          <Markdown>{item.explanation}</Markdown>
        </div>
      );
    case "tutor":
      return (
        <TutorBubble>
          <Markdown>{item.content}</Markdown>
        </TutorBubble>
      );
    case "error":
      return (
        <div data-testid="session-error">
          <Alert tone="error">
            {item.error.message}
            {item.error.retryable && " You can try that again."}
          </Alert>
        </div>
      );
    case "summary":
      return <SessionSummaryCard summary={item.summary} conceptNames={conceptNames} />;
  }
}

function FollowUp({ disabled, onAsk }: { disabled: boolean; onAsk: (text: string) => void }) {
  const [text, setText] = useState("");
  function submit(event: FormEvent) {
    event.preventDefault();
    const value = text.trim();
    if (!value || disabled) return;
    onAsk(value);
    setText("");
  }
  return (
    <form onSubmit={submit} className="flex gap-2">
      <input
        aria-label="Ask the tutor a question"
        value={text}
        onChange={(e) => setText(e.target.value)}
        maxLength={2000}
        placeholder="Ask a follow-up question…"
        className="block w-full rounded-lg border-0 px-3 py-2 text-sm ring-1 ring-inset ring-slate-300 focus:ring-2 focus:ring-brand-600"
      />
      <Button type="submit" variant="secondary" disabled={disabled || !text.trim()}>
        Ask
      </Button>
    </form>
  );
}

function LiveSession({ sessionId, wsBaseUrl }: { sessionId: string; wsBaseUrl: string }) {
  const { answer, ask, acknowledge, hint, end, reconnect } = useSessionChannel(sessionId, wsBaseUrl);
  const s = useSessionStore();
  const bottom = useRef<HTMLDivElement>(null);
  const [confirmEnd, setConfirmEnd] = useState(false);

  useEffect(() => {
    bottom.current?.scrollIntoView?.({ behavior: "smooth", block: "end" });
  }, [s.timeline]);

  const ended = s.awaiting === "ended" || s.status === "completed" || s.status === "abandoned";
  const conceptId = s.currentConceptId;
  const mastery = conceptId ? s.mastery[conceptId] : undefined;
  const streaming = s.timeline.some((t) => t.kind === "explanation" && t.streaming);
  const thinking = s.busy || (s.connection === "open" && s.awaiting === "none" && !ended);
  const connection = CONNECTION[s.connection];
  const targets = s.objective?.target_concepts ?? [];

  return (
    <div
      className="space-y-4"
      data-testid="session-room"
      data-awaiting={s.awaiting}
      data-status={s.status ?? ""}
      data-busy={s.busy}
    >
      <Card className="sticky top-0 z-10 space-y-3 p-4">
        <div className="flex flex-wrap items-center justify-between gap-3">
          <div className="flex min-w-0 flex-wrap items-center gap-2">
            <Badge className={connection.tone} data-testid="connection-status">
              {connection.label}
            </Badge>
            {targets.map((t) => (
              <Badge key={t.id} className={cn(t.id === conceptId && "bg-brand-50 text-brand-700")}>
                {t.name}
              </Badge>
            ))}
          </div>
          {!ended &&
            (confirmEnd ? (
              <div className="flex items-center gap-2 text-sm">
                <span className="text-slate-600">End and see your summary?</span>
                <Button variant="secondary" onClick={() => setConfirmEnd(false)}>
                  Keep going
                </Button>
                <Button
                  onClick={() => {
                    setConfirmEnd(false);
                    void end();
                  }}
                  disabled={s.busy && s.connection === "open"}
                >
                  End session
                </Button>
              </div>
            ) : (
              <Button variant="secondary" onClick={() => setConfirmEnd(true)}>
                End session
              </Button>
            ))}
        </div>
        {conceptId && mastery && (
          <MasteryBar name={s.conceptNames[conceptId] ?? "Current concept"} value={mastery.value} previous={mastery.previous} />
        )}
      </Card>

      {s.connection === "failed" && !ended && (
        <Alert tone="error">
          Lost the connection to your tutor. Your progress is saved.{" "}
          <button type="button" onClick={reconnect} className="font-medium underline">
            Reconnect
          </button>
        </Alert>
      )}

      <div className="space-y-4" aria-live="polite" aria-busy={streaming}>
        {s.timeline.length === 0 && s.connection !== "failed" && (
          <div className="space-y-2">
            <Skeleton className="h-5 w-48" />
            <Skeleton className="h-24 w-full max-w-3xl" />
          </div>
        )}
        {s.timeline.map((item) => (
          <Item
            key={item.id}
            item={item}
            activeQuestionId={s.awaiting === "answer" ? (s.currentQuestion?.question_id ?? null) : null}
            busy={s.busy}
            conceptNames={s.conceptNames}
            onAnswer={(questionId, content) => {
              if (s.currentQuestion?.question_id === questionId) answer(s.currentQuestion, content);
            }}
            onHint={hint}
          />
        ))}
        {thinking && !streaming && (
          <p data-testid="tutor-thinking" className="flex items-center gap-2 text-sm text-slate-500">
            <span className="size-2 animate-bounce rounded-full bg-brand-600" />
            Your tutor is thinking…
          </p>
        )}
        <div ref={bottom} />
      </div>

      {!ended && (s.awaiting === "acknowledgement" || s.awaiting === "answer") && (
        <Card className="space-y-3 p-4">
          {s.awaiting === "acknowledgement" && (
            <div className="flex flex-wrap gap-2">
              <Button onClick={() => acknowledge(true)} disabled={s.busy}>
                Got it
              </Button>
              <Button variant="secondary" onClick={() => acknowledge(false)} disabled={s.busy}>
                Explain differently
              </Button>
            </div>
          )}
          <FollowUp disabled={s.busy || streaming} onAsk={ask} />
        </Card>
      )}
    </div>
  );
}

/** A session: live when it is still running, otherwise its summary. */
export function SessionRoom({ sessionId, wsBaseUrl }: { sessionId: string; wsBaseUrl: string }) {
  const { data, isLoading, error } = useQuery({
    queryKey: [...sessionsKey, "detail", sessionId],
    queryFn: () => api.getSession(sessionId),
    // Read once per visit: the live channel owns the state from here on, and a refetch
    // mid-session must not swap the transcript for the stored view.
    staleTime: Infinity,
    gcTime: 0,
    refetchOnWindowFocus: false,
    refetchOnReconnect: false,
  });

  if (isLoading) return <Skeleton className="h-40 w-full" />;
  if (error || !data) {
    const notFound = error instanceof ApiError && error.status === 404;
    return (
      <Alert tone="error">
        {notFound ? "This session does not exist." : `Could not load the session. ${error?.message ?? ""}`}{" "}
        <Link href="/session" className="font-medium underline">
          Back to sessions
        </Link>
      </Alert>
    );
  }
  if (data.status === "completed" || data.status === "abandoned") {
    const names = Object.fromEntries(data.objective.target_concepts.map((t) => [t.id, t.name]));
    return (
      <div className="space-y-4">
        <Link href="/session" className="text-sm font-medium text-brand-700 hover:underline">
          ← All sessions
        </Link>
        {data.summary ? (
          <SessionSummaryCard summary={data.summary} conceptNames={names} />
        ) : (
          <Alert>This session ended before anything was recorded.</Alert>
        )}
      </div>
    );
  }
  return <LiveSession sessionId={sessionId} wsBaseUrl={wsBaseUrl} />;
}
