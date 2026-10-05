import { create } from "zustand";

import type {
  Awaiting,
  ErrorPayload,
  Evaluation,
  ExplanationKind,
  SessionObjective,
  SessionQuestion,
  SessionStatus,
  SessionSummary,
  SessionView,
  ServerEvent,
} from "@/types/session";

/**
 * Live state of the open study session, built from the server's event stream.
 *
 * `applyEvent` is a pure reducer (unit-tested); the store wraps it. The server is the
 * source of truth: every turn ends with `turn_complete`, which carries the authoritative
 * view (status, what is awaited, the open question) — the timeline is only what the
 * student has seen.
 */

export type TimelineItem =
  | {
      kind: "explanation";
      id: string;
      conceptId: string;
      conceptName: string;
      explainKind: ExplanationKind;
      content: string;
      streaming: boolean;
    }
  | { kind: "question"; id: string; question: SessionQuestion; answer: string | null }
  | { kind: "student"; id: string; text: string }
  | { kind: "evaluation"; id: string; evaluation: Evaluation }
  | { kind: "hint"; id: string; hintNumber: number; content: string }
  | { kind: "misconception"; id: string; name: string; explanation: string }
  | { kind: "tutor"; id: string; content: string }
  | { kind: "error"; id: string; error: ErrorPayload }
  | { kind: "summary"; id: string; summary: SessionSummary };

export interface MasteryState {
  value: number;
  /** Value before the latest update, for the change animation. */
  previous: number | null;
}

export type ConnectionState = "idle" | "connecting" | "open" | "reconnecting" | "closed" | "failed";

export interface SessionRoomState {
  sessionId: string | null;
  status: SessionStatus | null;
  awaiting: Awaiting;
  objective: SessionObjective | null;
  currentQuestion: SessionQuestion | null;
  currentConceptId: string | null;
  interactionCount: number;
  summary: SessionSummary | null;
  conceptNames: Record<string, string>;
  mastery: Record<string, MasteryState>;
  timeline: TimelineItem[];
  /** A message was sent and its turn has not completed yet. */
  busy: boolean;
  connection: ConnectionState;
  /** Skip a resumed explanation the student is already looking at. */
  skipping: boolean;
  seq: number;
}

export const initialRoomState: SessionRoomState = {
  sessionId: null,
  status: null,
  awaiting: "none",
  objective: null,
  currentQuestion: null,
  currentConceptId: null,
  interactionCount: 0,
  summary: null,
  conceptNames: {},
  mastery: {},
  timeline: [],
  busy: false,
  connection: "idle",
  skipping: false,
  seq: 0,
};

function withObjective(state: SessionRoomState, objective: SessionObjective | null | undefined) {
  if (!objective) return state;
  const conceptNames = { ...state.conceptNames };
  const mastery = { ...state.mastery };
  for (const target of objective.target_concepts ?? []) {
    conceptNames[target.id] = target.name;
    if (mastery[target.id] === undefined && typeof target.mastery === "number") {
      mastery[target.id] = { value: target.mastery, previous: null };
    }
  }
  return { ...state, objective, conceptNames, mastery };
}

type NewItem = TimelineItem extends infer T ? (T extends TimelineItem ? Omit<T, "id"> : never) : never;

function push(state: SessionRoomState, item: NewItem): SessionRoomState {
  const seq = state.seq + 1;
  return { ...state, seq, timeline: [...state.timeline, { id: `t${seq}`, ...item } as TimelineItem] };
}

function lastExplanationIndex(timeline: TimelineItem[]): number {
  for (let i = timeline.length - 1; i >= 0; i--) if (timeline[i].kind === "explanation") return i;
  return -1;
}

/** The view a REST call or `turn_complete` returned. */
export function applyView(state: SessionRoomState, view: SessionView): SessionRoomState {
  return {
    ...withObjective(state, view.objective),
    sessionId: view.session_id,
    status: view.status,
    awaiting: view.awaiting,
    currentQuestion: view.current_question,
    currentConceptId: view.current_concept_id,
    interactionCount: view.interaction_count,
    summary: view.summary ?? state.summary,
    busy: false,
    skipping: false,
  };
}

export function applyEvent(state: SessionRoomState, event: ServerEvent): SessionRoomState {
  switch (event.type) {
    case "session_started":
      return withObjective(state, event.payload.objective);

    case "explanation_start": {
      const { concept_id, concept_name, kind = "intro" } = event.payload;
      const name = concept_name || state.conceptNames[concept_id] || "";
      // A reconnect re-sends the explanation awaiting acknowledgement; keep the copy we have.
      if (kind === "resume" && lastExplanationIndex(state.timeline) >= 0) {
        return { ...state, skipping: true };
      }
      return push(
        { ...state, currentConceptId: concept_id || state.currentConceptId, skipping: false },
        {
          kind: "explanation",
          conceptId: concept_id,
          conceptName: name,
          explainKind: kind,
          content: "",
          streaming: true,
        },
      );
    }

    case "explanation_chunk": {
      if (state.skipping) return state;
      const index = lastExplanationIndex(state.timeline);
      const item = state.timeline[index];
      if (!item || item.kind !== "explanation" || !item.streaming) return state;
      const timeline = [...state.timeline];
      timeline[index] = { ...item, content: item.content + event.payload.content };
      return { ...state, timeline };
    }

    case "explanation_end": {
      if (state.skipping) return { ...state, skipping: false };
      const index = lastExplanationIndex(state.timeline);
      const item = state.timeline[index];
      if (!item || item.kind !== "explanation") return state;
      const timeline = [...state.timeline];
      timeline[index] = { ...item, streaming: false };
      return { ...state, timeline };
    }

    case "question": {
      const question = event.payload;
      const seen = state.timeline.some(
        (t) => t.kind === "question" && t.question.question_id === question.question_id,
      );
      const next = { ...state, currentQuestion: question, currentConceptId: question.concept_id };
      return seen ? next : push(next, { kind: "question", question, answer: null });
    }

    case "evaluation": {
      const evaluation = event.payload;
      const update = evaluation.mastery_update;
      const mastery = update
        ? {
            ...state.mastery,
            [update.concept_id]: { value: update.new_mastery, previous: update.old_mastery },
          }
        : state.mastery;
      return push({ ...state, mastery }, { kind: "evaluation", evaluation });
    }

    case "hint":
      return push(state, {
        kind: "hint",
        hintNumber: event.payload.hint_number,
        content: event.payload.content,
      });

    case "misconception_detected":
      return push(state, {
        kind: "misconception",
        name: event.payload.misconception_name,
        explanation: event.payload.explanation,
      });

    case "tutor_message":
      return push(state, { kind: "tutor", content: event.payload.content });

    case "session_ended": {
      const next = { ...state, summary: event.payload.summary, awaiting: "ended" as const };
      return state.timeline.some((t) => t.kind === "summary")
        ? next
        : push(next, { kind: "summary", summary: event.payload.summary });
    }

    case "error":
      return push({ ...state, busy: false }, { kind: "error", error: event.payload });

    case "turn_complete":
      return applyView(state, event.payload);

    default:
      return state;
  }
}

/** Echo of what the student sent (answers are attached to their question card instead). */
export function applyStudentText(state: SessionRoomState, text: string): SessionRoomState {
  return push({ ...state, busy: true }, { kind: "student", text });
}

export function applyAnswer(
  state: SessionRoomState,
  questionId: string,
  answer: string,
): SessionRoomState {
  const timeline = state.timeline.map((t) =>
    t.kind === "question" && t.question.question_id === questionId ? { ...t, answer } : t,
  );
  return { ...state, timeline, busy: true };
}

interface SessionStore extends SessionRoomState {
  reset: (sessionId: string) => void;
  receive: (event: ServerEvent) => void;
  view: (view: SessionView) => void;
  studentText: (text: string) => void;
  answer: (questionId: string, answer: string) => void;
  setBusy: (busy: boolean) => void;
  setConnection: (connection: ConnectionState) => void;
}

export const useSessionStore = create<SessionStore>()((set) => ({
  ...initialRoomState,
  reset: (sessionId) => set({ ...initialRoomState, sessionId }),
  receive: (event) => set((s) => applyEvent(s, event)),
  view: (view) => set((s) => applyView(s, view)),
  studentText: (text) => set((s) => applyStudentText(s, text)),
  answer: (questionId, answer) => set((s) => applyAnswer(s, questionId, answer)),
  setBusy: (busy) => set({ busy }),
  setConnection: (connection) => set({ connection }),
}));
