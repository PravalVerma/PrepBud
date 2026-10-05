import { act, render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { Markdown, normaliseMath } from "@/components/session/markdown";
import { MasteryBar } from "@/components/session/mastery-bar";
import { QuestionCard } from "@/components/session/question-card";
import { SessionHistory } from "@/components/session/session-history";
import { SessionRoom } from "@/components/session/session-room";
import { StartSessionForm } from "@/components/session/start-session-form";
import type { SessionDetail, SessionListItem, SessionQuestion, SessionSummary } from "@/types/session";

import { jsonResponse, meta, renderWithQuery } from "./test-utils";

vi.mock("next/link", () => ({
  default: (props: React.AnchorHTMLAttributes<HTMLAnchorElement>) => <a {...props} />,
}));
const push = vi.fn();
vi.mock("next/navigation", () => ({ useRouter: () => ({ push }) }));

const fetchMock = vi.fn<typeof fetch>();
beforeEach(() => vi.stubGlobal("fetch", fetchMock));
afterEach(() => {
  vi.unstubAllGlobals();
  fetchMock.mockReset();
  push.mockReset();
});

const ok = (data: unknown, status = 200) => jsonResponse(status, { data, meta });

const mcq: SessionQuestion = {
  question_id: "q1",
  concept_id: "c1",
  type: "mcq",
  difficulty: 0.3,
  content: "What is $\\frac{d}{dx}x^2$?",
  options: [
    { label: "A", text: "$2x$" },
    { label: "B", text: "$x$" },
  ],
  hints_available: 1,
};

const summary: SessionSummary = {
  duration_minutes: 12.4,
  concepts_covered: 1,
  questions_answered: 4,
  accuracy: 0.75,
  mastery_changes: [{ concept_id: "c1", concept: "Derivatives", from: 0.2, to: 0.55 }],
  end_reason: "student_ended",
  text: "Nice work on **derivatives**.",
  reviews: [{ concept_id: "c1", next_review_date: "2026-10-08", interval_days: 3 }],
};

describe("Markdown + LaTeX (AC-5.6)", () => {
  it("normalises bracket delimiters outside code", () => {
    expect(normaliseMath("a \\(x^2\\) b")).toBe("a $x^2$ b");
    expect(normaliseMath("\\[ \\int f \\]")).toBe("\n$$\n\\int f\n$$\n");
    expect(normaliseMath("Hence\n$$ a+b $$\nso $x$")).toBe("Hence\n$$\na+b\n$$\nso $x$");
    expect(normaliseMath("`\\(keep\\)` and ```\n\\[keep\\]\n```")).toBe("`\\(keep\\)` and ```\n\\[keep\\]\n```");
  });

  it("renders inline and display math with KaTeX", () => {
    const { container } = render(<Markdown>{"Inline \\(x^2\\), display:\n\n$$\\frac{a}{b}$$\n\nand **bold**"}</Markdown>);
    expect(container.querySelectorAll(".katex").length).toBeGreaterThanOrEqual(2);
    expect(container.querySelector(".katex-display")).not.toBeNull();
    expect(container.querySelector("strong")).toHaveTextContent("bold");
  });

  it("never renders raw HTML and survives bad LaTeX", () => {
    const { container } = render(
      <Markdown>{'<img src=x onerror="alert(1)"> <script>alert(1)</script> $\\badcommand{$ [x](javascript:alert(1))'}</Markdown>,
    );
    expect(container.querySelector("img")).toBeNull();
    expect(container.querySelector("script")).toBeNull();
    expect(container.querySelector("a")?.getAttribute("href") ?? "").not.toContain("javascript");
  });
});

describe("QuestionCard", () => {
  it("submits the chosen option label for MCQ", async () => {
    const onSubmit = vi.fn();
    const { container } = render(
      <QuestionCard question={mcq} answer={null} active disabled={false} onSubmit={onSubmit} onHint={vi.fn()} />,
    );
    expect(container.querySelector(".katex")).not.toBeNull(); // math in the question
    const submit = screen.getByRole("button", { name: "Submit answer" });
    expect(submit).toBeDisabled();
    await userEvent.click(screen.getAllByRole("radio")[0]);
    expect(screen.getAllByRole("radio")[0]).toHaveAttribute("aria-checked", "true");
    await userEvent.click(submit);
    expect(onSubmit).toHaveBeenCalledWith("A");
  });

  it("offers true/false and hints", async () => {
    const onSubmit = vi.fn();
    const onHint = vi.fn();
    render(
      <QuestionCard
        question={{ ...mcq, type: "true_false", options: null, content: "The sky is green." }}
        answer={null}
        active
        disabled={false}
        onSubmit={onSubmit}
        onHint={onHint}
      />,
    );
    await userEvent.click(screen.getByRole("button", { name: /Hint \(1 left\)/ }));
    expect(onHint).toHaveBeenCalled();
    await userEvent.click(screen.getByRole("radio", { name: "False" }));
    await userEvent.click(screen.getByRole("button", { name: "Submit answer" }));
    expect(onSubmit).toHaveBeenCalledWith("false");
  });

  it("takes a typed answer (Ctrl+Enter) and locks once answered", async () => {
    const onSubmit = vi.fn();
    const question = { ...mcq, type: "short_answer" as const, options: null, hints_available: 0 };
    const { rerender } = render(
      <QuestionCard question={question} answer={null} active disabled={false} onSubmit={onSubmit} onHint={vi.fn()} />,
    );
    expect(screen.getByRole("button", { name: "💡 Hint" })).toBeDisabled();
    await userEvent.type(screen.getByLabelText("Your answer"), "2x{Control>}{Enter}{/Control}");
    expect(onSubmit).toHaveBeenCalledWith("2x");

    rerender(<QuestionCard question={question} answer="2x" active disabled={false} onSubmit={onSubmit} onHint={vi.fn()} />);
    expect(screen.queryByLabelText("Your answer")).toBeNull();
    expect(screen.getByTestId("submitted-answer")).toHaveTextContent("Your answer: 2x");
    expect(screen.queryByRole("button", { name: "Submit answer" })).toBeNull();
  });
});

describe("MasteryBar (AC-5.4)", () => {
  it("shows the value, label and the change", async () => {
    render(<MasteryBar name="Limits" value={0.52} previous={0.45} />);
    expect(screen.getByRole("progressbar", { name: "Mastery of Limits" })).toHaveAttribute("aria-valuenow", "52");
    expect(screen.getByTestId("mastery-delta")).toHaveTextContent("+7%");
    expect(screen.getByTestId("mastery-value")).toHaveTextContent("52%");
    expect(screen.getByText("· intermediate")).toBeInTheDocument();
  });
});

describe("StartSessionForm", () => {
  it("creates a session and opens it", async () => {
    fetchMock.mockResolvedValue(
      ok({ session_id: "s9", status: "initialising", websocket_url: "/x", objective: { target_concepts: [] } }, 201),
    );
    renderWithQuery(<StartSessionForm />);
    await userEvent.selectOptions(screen.getByLabelText("Session type"), "practice");
    await userEvent.selectOptions(screen.getByLabelText("Time budget"), "30");
    await userEvent.click(screen.getByRole("button", { name: "Start session" }));

    await waitFor(() => expect(push).toHaveBeenCalledWith("/session/s9"));
    const [url, init] = fetchMock.mock.calls[0];
    expect(url).toBe("/api/backend/sessions");
    expect(JSON.parse(String(init?.body))).toEqual({ session_type: "practice", time_budget_minutes: 30 });
  });

  it("focuses on one concept and explains when there is nothing to study", async () => {
    fetchMock.mockImplementation(async (u) =>
      String(u).includes("/concepts/")
        ? ok({ id: "c1", name: "Derivatives" })
        : jsonResponse(422, {
            error: { code: "UNPROCESSABLE_ENTITY", message: "No concepts", details: { reason: "NO_CONCEPTS" } },
            meta,
          }),
    );
    renderWithQuery(<StartSessionForm conceptId="c1" />);
    expect(await screen.findByText("Focused on Derivatives.")).toBeInTheDocument();
    await userEvent.click(screen.getByRole("button", { name: "Start session" }));
    expect(await screen.findByRole("link", { name: "Upload some material" })).toHaveAttribute("href", "/upload");
    const body = JSON.parse(String(fetchMock.mock.calls.find(([u]) => u === "/api/backend/sessions")?.[1]?.body));
    expect(body).toMatchObject({ session_type: "teach", concept_ids: ["c1"] });
  });
});

describe("SessionHistory", () => {
  it("lists sessions with the right action", async () => {
    const base: SessionListItem = {
      id: "s1",
      session_type: "mixed",
      status: "completed",
      started_at: "2026-10-04T10:00:00Z",
      ended_at: "2026-10-04T10:12:00Z",
      duration_seconds: 720,
      interaction_count: 6,
      concepts: ["Derivatives"],
      summary,
    };
    fetchMock.mockResolvedValue(
      jsonResponse(200, {
        data: [{ ...base, id: "s2", status: "paused", summary: null, duration_seconds: null }, base],
        meta: { ...meta, pagination: { total: 2, page: 1, per_page: 10, total_pages: 1 } },
      }),
    );
    renderWithQuery(<SessionHistory />);
    const rows = await screen.findAllByTestId("session-history-item");
    expect(within(rows[0]).getByRole("link", { name: "Continue" })).toHaveAttribute("href", "/session/s2");
    expect(rows[0]).toHaveTextContent("Paused");
    expect(within(rows[1]).getByRole("link", { name: "View summary" })).toHaveAttribute("href", "/session/s1");
    expect(rows[1]).toHaveTextContent("12 min · 4 questions, 75% correct");
  });
});

// --- live session -------------------------------------------------------------------------

class FakeSocket {
  static last: FakeSocket | null = null;
  readyState = 0;
  sent: { type: string; payload: Record<string, unknown> }[] = [];
  onopen: (() => void) | null = null;
  onmessage: ((e: { data: string }) => void) | null = null;
  onclose: ((e: { code: number }) => void) | null = null;
  constructor(readonly url: string) {
    FakeSocket.last = this;
    queueMicrotask(() => {
      this.readyState = 1;
      this.onopen?.();
    });
  }
  send(data: string) {
    this.sent.push(JSON.parse(data));
  }
  close() {
    this.readyState = 3;
  }
  emit(...events: unknown[]) {
    act(() => {
      for (const e of events) this.onmessage?.({ data: JSON.stringify(e) });
    });
  }
}

const detail = (over: Partial<SessionDetail> = {}): SessionDetail => ({
  id: "s1",
  session_type: "teach",
  status: "initialising",
  started_at: null,
  ended_at: null,
  duration_seconds: null,
  interaction_count: 0,
  concepts: ["Derivatives"],
  summary: null,
  learning_goal_id: null,
  objective: { session_type: "teach", target_concepts: [{ id: "c1", name: "Derivatives", action: "teach", mastery: 0.2 }] },
  awaiting: "none",
  current_question: null,
  events: [],
  ...over,
});

const view = (over: Record<string, unknown> = {}) => ({
  type: "turn_complete",
  payload: {
    session_id: "s1",
    status: "active",
    awaiting: "acknowledgement",
    objective: detail().objective,
    current_question: null,
    current_concept_id: "c1",
    interaction_count: 1,
    summary: null,
    ...over,
  },
});

describe("SessionRoom", () => {
  beforeEach(() => {
    FakeSocket.last = null;
  });

  it("runs a session end to end over the WebSocket", async () => {
    vi.stubGlobal("WebSocket", FakeSocket);
    fetchMock.mockImplementation(async (u) =>
      String(u).endsWith("/ws-ticket")
        ? ok({ ticket: "tk", expires_in_seconds: 60, websocket_path: "/api/v1/sessions/s1/ws" })
        : ok(detail()),
    );
    renderWithQuery(<SessionRoom sessionId="s1" wsBaseUrl="ws://api.local" />);
    await waitFor(() => expect(screen.getByTestId("connection-status")).toHaveTextContent("Live"));
    const ws = FakeSocket.last!;
    expect(ws.url).toBe("ws://api.local/api/v1/sessions/s1/ws?ticket=tk");

    // AC-5.1: streamed explanation.
    ws.emit(
      { type: "session_started", payload: { session_id: "s1", objective: detail().objective } },
      { type: "explanation_start", payload: { concept_id: "c1", concept_name: "Derivatives", kind: "intro" } },
      { type: "explanation_chunk", payload: { content: "The derivative of $x^2$ " } },
    );
    expect(screen.getByTestId("explanation")).toHaveAttribute("data-streaming", "true");
    ws.emit({ type: "explanation_chunk", payload: { content: "is $2x$." } }, { type: "explanation_end", payload: {} }, view());
    expect(screen.getByTestId("explanation")).toHaveTextContent("The derivative of");
    expect(screen.getByTestId("explanation").querySelectorAll(".katex")).toHaveLength(2);
    expect(screen.getByRole("progressbar", { name: "Mastery of Derivatives" })).toHaveAttribute("aria-valuenow", "20");

    await userEvent.click(screen.getByRole("button", { name: "Got it" }));
    expect(ws.sent.at(-1)).toEqual({ type: "student_acknowledge", payload: { understood: true } });
    expect(screen.getByTestId("tutor-thinking")).toBeInTheDocument();

    // AC-5.2: question → answer → evaluation, AC-5.4: mastery moves.
    ws.emit({ type: "question", payload: mcq }, view({ awaiting: "answer", current_question: mcq, interaction_count: 2 }));
    await userEvent.click(screen.getAllByRole("radio")[1]);
    await userEvent.click(screen.getByRole("button", { name: "Submit answer" }));
    expect(ws.sent.at(-1)).toMatchObject({ type: "student_response", payload: { content: "B", question_id: "q1" } });
    ws.emit(
      {
        type: "evaluation",
        payload: {
          question_id: "q1",
          is_correct: false,
          score: 0,
          explanation: "Use the power rule.",
          correct_answer: "A) $2x$",
          mastery_update: { concept_id: "c1", old_mastery: 0.2, new_mastery: 0.16, label: "novice" },
        },
      },
      { type: "misconception_detected", payload: { misconception_name: "forgot_power_rule", explanation: "Bring the power down." } },
      view({ awaiting: "acknowledgement", interaction_count: 3 }),
    );
    const evaluation = screen.getByTestId("evaluation");
    expect(evaluation).toHaveAttribute("data-correct", "false");
    expect(evaluation).toHaveTextContent("Not quite");
    expect(evaluation).toHaveTextContent("Correct answer:");
    expect(screen.getByTestId("misconception")).toHaveTextContent("forgot power rule");
    expect(screen.getAllByRole("progressbar", { name: "Mastery of Derivatives" })[0]).toHaveAttribute("aria-valuenow", "16");

    // Follow-up and "explain differently".
    await userEvent.type(screen.getByLabelText("Ask the tutor a question"), "Why?");
    await userEvent.click(screen.getByRole("button", { name: "Ask" }));
    expect(ws.sent.at(-1)).toEqual({ type: "student_question", payload: { content: "Why?" } });
    ws.emit(view());
    await userEvent.click(screen.getByRole("button", { name: "Explain differently" }));
    expect(ws.sent.at(-1)).toEqual({ type: "student_acknowledge", payload: { understood: false } });

    // AC-5.7: an AI outage is shown and the student can try again.
    ws.emit({ type: "error", payload: { code: "LLM_UNAVAILABLE", message: "The tutor is unavailable.", retryable: true } }, view());
    expect(screen.getByTestId("session-error")).toHaveTextContent("The tutor is unavailable. You can try that again.");
    expect(screen.getByRole("button", { name: "Got it" })).toBeEnabled();

    // AC-5.5: end → summary.
    await userEvent.click(screen.getByRole("button", { name: "End session" }));
    await userEvent.click(screen.getByRole("button", { name: "End session" }));
    expect(ws.sent.at(-1)).toEqual({ type: "end_session", payload: {} });
    ws.emit({ type: "session_ended", payload: { summary } }, view({ status: "completed", awaiting: "ended", summary }));
    const card = screen.getByTestId("session-summary");
    expect(card).toHaveTextContent("Nice work on derivatives.");
    expect(card).toHaveTextContent("75%");
    expect(card).toHaveTextContent("You ended the session");
    expect(screen.queryByRole("button", { name: "End session" })).toBeNull();
  });

  it("shows the summary of a finished session without connecting", async () => {
    const socket = vi.fn();
    vi.stubGlobal("WebSocket", socket);
    fetchMock.mockResolvedValue(ok(detail({ status: "completed", summary })));
    renderWithQuery(<SessionRoom sessionId="s1" wsBaseUrl="ws://api.local" />);
    expect(await screen.findByTestId("session-summary")).toHaveTextContent("12 min");
    expect(screen.getAllByText("Derivatives").length).toBeGreaterThan(0); // mastery + next review
    expect(socket).not.toHaveBeenCalled();
  });

  it("reports a missing session", async () => {
    fetchMock.mockResolvedValue(
      jsonResponse(404, { error: { code: "RESOURCE_NOT_FOUND", message: "Session not found", details: {} }, meta }),
    );
    renderWithQuery(<SessionRoom sessionId="nope" wsBaseUrl="ws://api.local" />);
    expect(await screen.findByText(/This session does not exist/)).toBeInTheDocument();
  });

  it("ends over REST when the connection is down", async () => {
    vi.stubGlobal("WebSocket", FakeSocket);
    fetchMock.mockImplementation(async (u, init) => {
      const url = String(u);
      if (url.endsWith("/ws-ticket")) return ok({ ticket: "tk", expires_in_seconds: 60, websocket_path: "/ws" });
      if (url.endsWith("/end") && init?.method === "POST") {
        return ok({ ...view({ status: "completed", awaiting: "ended", summary }).payload, events: [{ type: "session_ended", payload: { summary } }] });
      }
      return ok(detail({ status: "paused" }));
    });
    renderWithQuery(<SessionRoom sessionId="s1" wsBaseUrl="ws://api.local" />);
    await waitFor(() => expect(screen.getByTestId("connection-status")).toHaveTextContent("Live"));
    FakeSocket.last!.readyState = 3; // the connection silently died
    await userEvent.click(await screen.findByRole("button", { name: "End session" }));
    await userEvent.click(screen.getByRole("button", { name: "End session" }));
    expect(await screen.findByTestId("session-summary")).toBeInTheDocument();
  });
});
