import { describe, expect, it } from "vitest";

import {
  applyAnswer,
  applyEvent,
  applyStudentText,
  initialRoomState,
  type SessionRoomState,
} from "@/stores/session-store";
import type { ServerEvent, SessionQuestion, SessionView } from "@/types/session";

const objective = {
  session_type: "teach" as const,
  target_concepts: [
    { id: "c1", name: "Limits", action: "teach", mastery: 0.2 },
    { id: "c2", name: "Vectors", action: "practice" },
  ],
};

const question: SessionQuestion = {
  question_id: "q1",
  concept_id: "c1",
  type: "mcq",
  difficulty: 0.4,
  content: "Pick one",
  options: [
    { label: "A", text: "$2x$" },
    { label: "B", text: "$x$" },
  ],
  hints_available: 2,
};

const view = (over: Partial<SessionView> = {}): SessionView => ({
  session_id: "s1",
  status: "active",
  awaiting: "acknowledgement",
  objective,
  current_question: null,
  current_concept_id: "c1",
  interaction_count: 1,
  summary: null,
  ...over,
});

function run(events: ServerEvent[], state: SessionRoomState = initialRoomState) {
  return events.reduce(applyEvent, state);
}

const explanation = (content: string[], kind?: "intro" | "resume"): ServerEvent[] => [
  { type: "explanation_start", payload: { concept_id: "c1", concept_name: "Limits", kind } },
  ...content.map((c) => ({ type: "explanation_chunk" as const, payload: { content: c } })),
  { type: "explanation_end", payload: {} },
];

describe("session reducer", () => {
  it("builds a streamed explanation from chunks", () => {
    const mid = run([{ type: "session_started", payload: { session_id: "s1", objective } }, ...explanation(["A limit ", "is"]).slice(0, 3)]);
    expect(mid.timeline).toHaveLength(1);
    expect(mid.timeline[0]).toMatchObject({ kind: "explanation", content: "A limit is", streaming: true, conceptName: "Limits" });
    expect(mid.conceptNames).toEqual({ c1: "Limits", c2: "Vectors" });
    expect(mid.mastery).toEqual({ c1: { value: 0.2, previous: null } }); // c2 has no starting value

    const done = run([{ type: "explanation_end", payload: {} }], mid);
    expect(done.timeline[0]).toMatchObject({ streaming: false });
  });

  it("ignores chunks with no open explanation", () => {
    const state = run([{ type: "explanation_chunk", payload: { content: "x" } }, { type: "explanation_end", payload: {} }]);
    expect(state.timeline).toEqual([]);
  });

  it("takes the authoritative view from turn_complete", () => {
    const state = run([{ type: "turn_complete", payload: view({ awaiting: "answer", current_question: question }) }], {
      ...initialRoomState,
      busy: true,
    });
    expect(state).toMatchObject({ awaiting: "answer", status: "active", busy: false, interactionCount: 1, sessionId: "s1" });
    expect(state.currentQuestion?.question_id).toBe("q1");
  });

  it("de-duplicates what a reconnect re-sends", () => {
    let state = run([...explanation(["Hello"]), { type: "question", payload: question }]);
    // Reconnect: the server re-sends the open question and a resumed explanation.
    state = run([{ type: "question", payload: question }, ...explanation(["Hello again"], "resume")], state);
    expect(state.timeline.map((t) => t.kind)).toEqual(["explanation", "question"]);
    expect(state.timeline[0]).toMatchObject({ content: "Hello" });
    expect(state.skipping).toBe(false);
  });

  it("shows a resumed explanation on a fresh page", () => {
    const state = run(explanation(["Where we were"], "resume"));
    expect(state.timeline[0]).toMatchObject({ explainKind: "resume", content: "Where we were" });
  });

  it("records answers, evaluations and mastery changes", () => {
    let state = run([{ type: "question", payload: question }]);
    state = applyAnswer(state, "q1", "A");
    expect(state.busy).toBe(true);
    expect(state.timeline[0]).toMatchObject({ kind: "question", answer: "A" });

    state = run(
      [
        {
          type: "evaluation",
          payload: {
            question_id: "q1",
            is_correct: true,
            score: 1,
            explanation: "Yes",
            correct_answer: null,
            mastery_update: { concept_id: "c1", old_mastery: 0.2, new_mastery: 0.31, label: "beginner" },
          },
        },
      ],
      state,
    );
    expect(state.mastery.c1).toEqual({ value: 0.31, previous: 0.2 });
    expect(state.timeline.at(-1)?.kind).toBe("evaluation");
  });

  it("adds hints, misconceptions, tutor messages, errors and one summary", () => {
    const summary = {
      duration_minutes: 3,
      concepts_covered: 1,
      questions_answered: 1,
      accuracy: 1,
      mastery_changes: [],
    };
    let state = applyStudentText(initialRoomState, "Why?");
    state = run(
      [
        { type: "hint", payload: { hint_number: 1, content: "Think" } },
        { type: "misconception_detected", payload: { misconception_name: "sign_error", explanation: "Careful" } },
        { type: "tutor_message", payload: { kind: "encouragement", content: "Keep going" } },
        { type: "error", payload: { code: "LLM_UNAVAILABLE", message: "Down", retryable: true } },
        { type: "session_ended", payload: { summary } },
        { type: "session_ended", payload: { summary } },
        { type: "pong", payload: {} },
      ],
      state,
    );
    expect(state.timeline.map((t) => t.kind)).toEqual(["student", "hint", "misconception", "tutor", "error", "summary"]);
    expect(state.busy).toBe(false); // the error released the turn
    expect(state.awaiting).toBe("ended");
    expect(state.summary).toEqual(summary);
  });
});
