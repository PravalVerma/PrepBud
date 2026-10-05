/** Learning-session types (API_CONTRACT §3.8–3.9). */

import type { MasteryLabel } from "@/types/domain";

export type SessionType = "teach" | "practice" | "review" | "mixed";
export type SessionStatus = "initialising" | "active" | "paused" | "completed" | "abandoned";
/** What the session will accept next. */
export type Awaiting = "none" | "acknowledgement" | "answer" | "ended";
export type QuestionType = "mcq" | "true_false" | "short_answer" | "worked_problem" | "open_ended";
export type ExplanationKind =
  | "intro"
  | "retry"
  | "worked_example"
  | "socratic"
  | "followup"
  | "resume";

export interface TargetConcept {
  id: string;
  name: string;
  action: string;
  reason?: string;
  mastery?: number;
}

export interface SessionObjective {
  target_concepts: TargetConcept[];
  session_type: SessionType;
  estimated_duration_minutes?: number;
}

export interface SessionCreate {
  session_type?: SessionType;
  learning_goal_id?: string;
  time_budget_minutes?: number;
  concept_ids?: string[];
  preferences?: { max_concepts?: number };
}

export interface SessionCreated {
  session_id: string;
  status: SessionStatus;
  websocket_url: string;
  objective: SessionObjective;
}

export interface QuestionOption {
  label: string;
  text: string;
}

export interface SessionQuestion {
  question_id: string;
  concept_id: string;
  type: QuestionType;
  difficulty: number;
  content: string;
  options: QuestionOption[] | null;
  hints_available: number;
  mode?: "practice" | "review";
}

export interface MasteryUpdate {
  concept_id: string;
  old_mastery: number;
  new_mastery: number;
  label: MasteryLabel;
}

export interface Evaluation {
  question_id: string;
  is_correct: boolean;
  score: number;
  explanation: string;
  correct_answer: string | null;
  mastery_update: MasteryUpdate | null;
}

export interface SessionSummary {
  duration_minutes: number;
  concepts_covered: number;
  questions_answered: number;
  accuracy: number;
  mastery_changes: { concept_id?: string; concept: string; from: number; to: number }[];
  misconceptions_found?: number;
  end_reason?: string;
  text?: string;
  reviews?: { concept_id: string; next_review_date: string; interval_days: number }[];
}

/** The state after a turn — sent as the `turn_complete` payload and by the REST endpoints. */
export interface SessionView {
  session_id: string;
  status: SessionStatus;
  awaiting: Awaiting;
  objective: SessionObjective;
  current_question: SessionQuestion | null;
  current_concept_id: string | null;
  interaction_count: number;
  summary: SessionSummary | null;
  events?: ServerEvent[];
}

export interface SessionListItem {
  id: string;
  session_type: SessionType;
  status: SessionStatus;
  started_at: string | null;
  ended_at: string | null;
  duration_seconds: number | null;
  interaction_count: number;
  concepts: string[];
  summary: SessionSummary | null;
}

export interface SessionEventRecord {
  index: number;
  type: string;
  concept_id: string | null;
  payload: Record<string, unknown>;
  created_at: string | null;
}

export interface SessionDetail extends SessionListItem {
  learning_goal_id: string | null;
  objective: SessionObjective;
  awaiting: Awaiting | null;
  current_question: SessionQuestion | null;
  events: SessionEventRecord[];
}

export interface WsTicket {
  ticket: string;
  expires_in_seconds: number;
  websocket_path: string;
}

export interface ErrorPayload {
  code: string;
  message: string;
  retryable?: boolean;
  [key: string]: unknown;
}

/** Server → client messages. */
export type ServerEvent =
  | { type: "session_started"; payload: { session_id: string; objective: SessionObjective } }
  | {
      type: "explanation_start";
      payload: { concept_id: string; concept_name: string; kind?: ExplanationKind };
    }
  | { type: "explanation_chunk"; payload: { content: string } }
  | { type: "explanation_end"; payload: Record<string, never> }
  | { type: "question"; payload: SessionQuestion }
  | { type: "evaluation"; payload: Evaluation }
  | { type: "hint"; payload: { hint_number: number; content: string; question_id?: string } }
  | {
      type: "misconception_detected";
      payload: { misconception_name: string; explanation: string; status?: string };
    }
  | { type: "tutor_message"; payload: { kind: string; content: string } }
  | { type: "session_ended"; payload: { summary: SessionSummary } }
  | { type: "error"; payload: ErrorPayload }
  | { type: "turn_complete"; payload: SessionView }
  | { type: "pong"; payload: Record<string, never> };

/** Client → server messages. */
export type ClientMessage =
  | {
      type: "student_response";
      payload: { content: string; question_id: string; time_taken_seconds?: number };
    }
  | { type: "student_question"; payload: { content: string } }
  | { type: "student_acknowledge"; payload: { understood: boolean } }
  | { type: "request_hint"; payload: Record<string, never> }
  | { type: "end_session"; payload: Record<string, never> };
