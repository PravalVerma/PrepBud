import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { GoalForm } from "@/components/goals/goal-form";
import { GoalList } from "@/components/goals/goal-list";
import { ReviewQueue } from "@/components/review/review-queue";
import { groupPlan, StudyPlanView } from "@/components/review/study-plan";
import { addDays, isoDay } from "@/lib/utils";
import type { Goal, ReviewItem, StudyPlan } from "@/types/study";

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

const TODAY = isoDay(new Date());
const ok = (data: unknown, status = 200) => jsonResponse(status, { data, meta });
const page = (data: unknown[]) =>
  jsonResponse(200, { data, meta: { ...meta, pagination: { total: data.length, page: 1, per_page: 50, total_pages: 1 } } });
const bodyOf = (path: string, method = "POST") =>
  JSON.parse(
    String([...fetchMock.mock.calls].reverse().find(([u, i]) => u === `/api/backend${path}` && i?.method === method)?.[1]?.body),
  ); // latest matching call

function item(over: Partial<ReviewItem>): ReviewItem {
  return {
    id: over.id ?? Math.random().toString(36),
    concept_id: "c1",
    concept_name: "Limits",
    scheduled_date: TODAY,
    priority: 0.5,
    status: "pending",
    kind: "learn",
    goal_id: null,
    mastery_level: 0.2,
    mastery_label: "beginner",
    next_review_at: null,
    completed_at: null,
    ...over,
  };
}

function plan(items: ReviewItem[], stats: Partial<StudyPlan["stats"]> = {}): StudyPlan {
  return {
    id: "p1",
    learning_goal_id: null,
    status: "active",
    generated_at: null,
    valid_until: null,
    items,
    stats: {
      total_items: items.length,
      completed: 0,
      skipped: 0,
      overdue: 0,
      upcoming_today: 0,
      due_today: 0,
      reviews_due: 0,
      next_due_date: null,
      estimated_minutes: 45,
      ...stats,
    },
  };
}

const created = () =>
  ok({ session_id: "s1", status: "initialising", websocket_url: "/x", objective: { target_concepts: [] } }, 201);

describe("groupPlan (AC-6.2)", () => {
  it("puts overdue first, then days, done last", () => {
    const groups = groupPlan(
      [
        item({ id: "a", scheduled_date: addDays(TODAY, 2) }),
        item({ id: "b", status: "completed" }),
        item({ id: "c", status: "overdue", scheduled_date: addDays(TODAY, -3) }),
        item({ id: "d" }),
        item({ id: "e", scheduled_date: addDays(TODAY, -1) }), // stale pending → today
        item({ id: "f", status: "skipped" }),
      ],
      TODAY,
    );
    expect(groups.map((g) => [g.label, g.items.map((i) => i.id)])).toEqual([
      ["Overdue", ["c"]],
      ["Today", ["d", "e"]],
      [groups[2].label, ["a"]],
      ["Done today", ["b", "f"]],
    ]);
  });
});

describe("ReviewQueue (AC-6.6)", () => {
  it("shows what is due and starts a review or today's plan", async () => {
    const data = plan(
      [
        item({ id: "r", concept_id: "c9", kind: "review", status: "overdue", scheduled_date: addDays(TODAY, -2) }),
        item({ id: "l1", concept_id: "c1" }),
        item({ id: "l2", concept_id: "c2" }),
        item({ id: "later", concept_id: "c3", scheduled_date: addDays(TODAY, 1) }),
      ],
      { due_today: 3, overdue: 1, reviews_due: 1, upcoming_today: 2 },
    );
    fetchMock.mockImplementation(async (u) => (String(u).endsWith("/study-plan") ? ok(data) : created()));
    renderWithQuery(<ReviewQueue />);
    expect(await screen.findByTestId("due-count")).toHaveTextContent("3 items due today");
    expect(screen.getByText("1 overdue · 1 review · 2 to learn")).toBeInTheDocument();

    await userEvent.click(screen.getByRole("button", { name: "Start review" }));
    await waitFor(() => expect(push).toHaveBeenCalledWith("/session/s1"));
    expect(bodyOf("/sessions")).toEqual({ session_type: "review", time_budget_minutes: 15 });

    fetchMock.mockClear();
    await userEvent.click(screen.getByRole("button", { name: "Study today's plan" }));
    await waitFor(() => expect(fetchMock).toHaveBeenCalled());
    expect(bodyOf("/sessions")).toEqual({ session_type: "mixed", time_budget_minutes: 30, concept_ids: ["c1", "c2"] });
  });

  it("says when you are caught up and explains failures", async () => {
    fetchMock.mockImplementation(async (u) =>
      String(u).endsWith("/study-plan")
        ? ok(plan([], { next_due_date: addDays(TODAY, 1) }))
        : jsonResponse(422, {
            error: { code: "UNPROCESSABLE_ENTITY", message: "Nothing to review yet", details: { reason: "NO_CONCEPTS" } },
            meta,
          }),
    );
    renderWithQuery(<ReviewQueue compact />);
    expect(await screen.findByTestId("due-count")).toHaveTextContent("You're all caught up");
    expect(screen.getByText("Next item tomorrow.")).toBeInTheDocument();
    expect(screen.getByRole("heading", { name: "Due for review" })).toBeInTheDocument();
    await userEvent.click(screen.getByRole("button", { name: "Review anyway" }));
    expect(await screen.findByText("Nothing to review yet")).toBeInTheDocument();
  });
});

describe("StudyPlanView", () => {
  it("lists items and updates them", async () => {
    const data = plan([
      item({ id: "o", concept_name: "Vectors", kind: "review", status: "overdue", scheduled_date: addDays(TODAY, -1) }),
      item({ id: "t", concept_name: "Limits" }),
      item({ id: "s", concept_name: "Chain Rule", status: "skipped" }),
    ]);
    fetchMock.mockImplementation(async (u, init) => {
      if (init?.method === "PATCH") return ok(item({}));
      if (String(u).endsWith("/regenerate")) return ok(plan([]));
      if (String(u).endsWith("/sessions")) return created();
      return ok(data);
    });
    renderWithQuery(<StudyPlanView />);
    const overdue = await screen.findByTestId("plan-group-overdue");
    expect(overdue).toHaveTextContent("Overdue · 1");
    expect(within(overdue).getByText("Review")).toBeInTheDocument();
    expect(within(overdue).getByText(/due yesterday/)).toBeInTheDocument();

    const today = screen.getByTestId("plan-group-" + TODAY);
    await userEvent.click(within(today).getByRole("button", { name: "Skip" }));
    await waitFor(() => expect(bodyOf("/study-plan/items/t", "PATCH")).toEqual({ status: "skipped" }));
    await userEvent.click(within(today).getByRole("button", { name: "Later" }));
    await waitFor(() =>
      expect(bodyOf("/study-plan/items/t", "PATCH")).toEqual({ scheduled_date: addDays(TODAY, 1) }),
    );
    await userEvent.click(within(screen.getByTestId("plan-group-done")).getByRole("button", { name: "Undo" }));
    await waitFor(() => expect(bodyOf("/study-plan/items/s", "PATCH")).toEqual({ status: "pending" }));

    await userEvent.click(within(overdue).getByRole("button", { name: "Study" }));
    await waitFor(() => expect(push).toHaveBeenCalledWith("/session/s1"));
    expect(bodyOf("/sessions")).toMatchObject({ session_type: "review", concept_ids: ["c1"] });

    await userEvent.click(screen.getByRole("button", { name: "Rebuild plan" }));
    expect(await screen.findByText(/Your plan is empty/)).toBeInTheDocument();
  });
});

const goal = (over: Partial<Goal> = {}): Goal => ({
  id: "g1",
  title: "Master calculus",
  description: "Chapter 5",
  goal_type: "deadline",
  target_date: addDays(TODAY, 3),
  status: "active",
  subject_id: null,
  course_id: null,
  target_concept_ids: ["c1"],
  progress: { concept_count: 4, mastered_count: 1, average_mastery: 0.4, progress: 0.5, days_remaining: 3, all_mastered: false },
  created_at: null,
  updated_at: null,
  ...over,
});

describe("Goals", () => {
  it("creates a goal from chosen concepts", async () => {
    const onDone = vi.fn();
    fetchMock.mockImplementation(async (u, init) => {
      const url = String(u);
      if (url.startsWith("/api/backend/concepts")) {
        return page([
          { id: "c1", name: "Limits", mastery: { level: 0.1 } },
          { id: "c2", name: "Derivatives", mastery: { level: 0.5 } },
        ]);
      }
      if (url.startsWith("/api/backend/subjects")) return page([{ id: "s1", name: "Calculus" }]);
      if (init?.method === "POST") return ok(goal(), 201);
      return page([]);
    });
    renderWithQuery(<GoalForm onDone={onDone} />);
    const submit = screen.getByRole("button", { name: "Create goal & plan" });
    await userEvent.type(screen.getByLabelText("Goal"), "Master differentiation");
    expect(submit).toBeDisabled(); // no concepts yet
    await userEvent.click(await screen.findByRole("checkbox", { name: /Derivatives/ }));
    await userEvent.click(screen.getByRole("checkbox", { name: /Limits/ }));
    await userEvent.selectOptions(screen.getByLabelText("Kind of goal"), "deadline");
    expect(submit).toBeDisabled(); // a deadline needs a date
    await userEvent.type(screen.getByLabelText("Target date"), addDays(TODAY, 7));
    await userEvent.click(submit);

    await waitFor(() => expect(onDone).toHaveBeenCalled());
    expect(bodyOf("/goals")).toEqual({
      title: "Master differentiation",
      description: null,
      goal_type: "deadline",
      target_date: addDays(TODAY, 7),
      subject_id: null,
      course_id: null,
      target_concept_ids: ["c2", "c1"],
    });
  });

  it("scopes a goal to a subject and edits an existing goal", async () => {
    fetchMock.mockImplementation(async (u, init) => {
      const url = String(u);
      if (url.startsWith("/api/backend/subjects")) return page([{ id: "s1", name: "Calculus" }]);
      if (url.startsWith("/api/backend/concepts")) return page([]);
      if (init?.method === "PATCH") return ok(goal());
      return page([]);
    });
    renderWithQuery(<GoalForm goal={goal({ goal_type: "mastery", target_date: null })} onDone={vi.fn()} />);
    expect(screen.getByLabelText("Goal")).toHaveValue("Master calculus");
    await userEvent.selectOptions(screen.getByLabelText("What to study"), "subject");
    await userEvent.selectOptions(await screen.findByLabelText("Subject"), "s1");
    await userEvent.click(screen.getByRole("button", { name: "Save goal" }));
    await waitFor(() =>
      expect(bodyOf("/goals/g1", "PATCH")).toMatchObject({ subject_id: "s1", target_concept_ids: [] }),
    );
  });

  it("shows progress and manages goals", async () => {
    const goals = [goal(), goal({ id: "g2", title: "Vectors", status: "paused", target_date: null, progress: { ...goal().progress, all_mastered: true, mastered_count: 4, progress: 1, days_remaining: null } })];
    fetchMock.mockImplementation(async (u, init) => {
      if (init?.method === "PATCH") return ok(goal());
      if (init?.method === "DELETE") return new Response(null, { status: 204 });
      return page(String(u).includes("status=active") ? [goals[0]] : goals);
    });
    renderWithQuery(<GoalList />);
    const [card] = await screen.findAllByTestId("goal-card");
    expect(card).toHaveTextContent("1 of 4 concepts mastered");
    expect(card).toHaveTextContent("3 days left");
    expect(within(card).getByRole("progressbar")).toHaveAttribute("aria-valuenow", "50");
    expect(within(card).getByRole("link", { name: "Study now" })).toHaveAttribute("href", "/session?goal_id=g1");

    await userEvent.click(within(card).getByRole("button", { name: "Pause" }));
    await waitFor(() => expect(bodyOf("/goals/g1", "PATCH")).toEqual({ status: "paused" }));
    await userEvent.click(within(card).getByRole("button", { name: "Delete" }));
    await userEvent.click(within(card).getByRole("button", { name: "Delete goal" }));
    await waitFor(() =>
      expect(fetchMock.mock.calls.some(([u, i]) => u === "/api/backend/goals/g1" && i?.method === "DELETE")).toBe(true),
    );

    await userEvent.click(screen.getByRole("tab", { name: "All" }));
    const cards = await screen.findAllByTestId("goal-card");
    expect(cards).toHaveLength(2);
    expect(cards[1]).toHaveTextContent("Paused");
    expect(within(cards[1]).getByRole("button", { name: "Resume" })).toBeInTheDocument();
  });
});
