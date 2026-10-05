import { QueryClient, QueryClientProvider, useMutation } from "@tanstack/react-query";
import { act, render, renderHook, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { Providers } from "@/app/providers";
import { layoutGraph, ConceptGraphView } from "@/components/concepts/concept-graph";
import { ActivityChart } from "@/components/dashboard/activity-chart";
import { MasteryOverviewCard } from "@/components/dashboard/mastery-overview-card";
import { MisconceptionsCard } from "@/components/dashboard/misconceptions-card";
import { derivedState, OnboardingChecklist } from "@/components/dashboard/onboarding-checklist";
import { AIUsageCard } from "@/components/progress/ai-usage-card";
import { MasteryHeatmap } from "@/components/progress/mastery-heatmap";
import { MisconceptionList } from "@/components/progress/misconception-list";
import { useProcessingToasts } from "@/components/upload/document-list";
import { isoDay } from "@/lib/utils";
import { useToastStore } from "@/stores/toast-store";
import type { AIUsage, MasteryHeatmap as Heatmap, MasteryOverview, StudentMisconception } from "@/types/dashboard";
import type { ConceptGraph, DocumentSummary, Profile } from "@/types/domain";

import { jsonResponse, meta, renderWithQuery } from "./test-utils";

vi.mock("next/link", () => ({
  default: (props: React.AnchorHTMLAttributes<HTMLAnchorElement>) => <a {...props} />,
}));
const push = vi.fn();
vi.mock("next/navigation", () => ({ useRouter: () => ({ push }) }));

const fetchMock = vi.fn<typeof fetch>();
beforeEach(() => {
  vi.stubGlobal("fetch", fetchMock);
  useToastStore.setState({ toasts: [] });
});
afterEach(() => {
  vi.unstubAllGlobals();
  fetchMock.mockReset();
  push.mockReset();
});

const ok = (data: unknown) => jsonResponse(200, { data, meta });
const page = (data: unknown[], total = data.length) =>
  jsonResponse(200, { data, meta: { ...meta, pagination: { total, page: 1, per_page: 20, total_pages: total > 20 ? 2 : 1 } } });
const urlOf = (call: Parameters<typeof fetch>) => String(call[0]);

const TODAY = isoDay(new Date());
const overview: MasteryOverview = {
  subjects: [
    { id: "s1", name: "Maths", avg_mastery: 0.525, concept_count: 2, mastered_count: 1, struggling_count: 1 },
    { id: null, name: "Unsorted", avg_mastery: 0, concept_count: 1, mastered_count: 0, struggling_count: 0 },
  ],
  overall_stats: {
    total_concepts: 3,
    total_mastered: 1,
    in_progress: 1,
    avg_mastery: 0.35,
    streak_days: 2,
    total_study_minutes: 135,
    total_sessions: 4,
    questions_answered: 20,
    accuracy: 0.75,
  },
  mastery_distribution: { novice: 1, beginner: 1, intermediate: 0, proficient: 0, mastered: 1 },
  recent_activity: Array.from({ length: 30 }, (_, i) => ({
    date: isoDay(new Date(Date.now() - (29 - i) * 86_400_000)),
    sessions: i === 29 ? 1 : 0,
    minutes: i === 29 ? 25 : i === 28 ? 0 : 0,
    questions: i === 29 ? 4 : i === 27 ? 2 : 0,
    concepts_practiced: i === 29 ? 2 : 0,
  })),
  misconceptions: { active: 1 },
  timezone: "UTC",
  generated_at: new Date().toISOString(),
};

describe("Dashboard widgets (AC-7.1)", () => {
  it("shows overall, distribution and per-subject mastery", async () => {
    fetchMock.mockResolvedValue(ok(overview));
    renderWithQuery(<MasteryOverviewCard />);
    const card = await screen.findByTestId("mastery-overview");
    expect(card).toHaveTextContent("1 / 3");
    expect(card).toHaveTextContent("1 in progress");
    expect(card).toHaveTextContent("35%");
    expect(card).toHaveTextContent("2 days");
    expect(card).toHaveTextContent("2 h 15 min");
    expect(screen.getByTestId("mastery-distribution")).toHaveTextContent("novice 1");
    const subjects = within(screen.getByTestId("subject-mastery")).getAllByRole("listitem");
    expect(within(subjects[0]).getByRole("link", { name: "Maths" })).toHaveAttribute("href", "/concepts?subject_id=s1");
    expect(subjects[0]).toHaveTextContent("1/2 mastered · 1 struggling · 53%");
    expect(within(subjects[1]).queryByRole("link")).toBeNull();
    expect(screen.getByRole("progressbar", { name: "Average mastery in Maths" })).toHaveAttribute("aria-valuenow", "53");
  });

  it("handles errors and empty data", async () => {
    fetchMock.mockResolvedValue(jsonResponse(404, { error: { code: "RESOURCE_NOT_FOUND", message: "x", details: {} }, meta }));
    renderWithQuery(<MasteryOverviewCard />);
    expect(await screen.findByText("Progress is unavailable right now.")).toBeInTheDocument();
  });

  it("charts daily activity", async () => {
    fetchMock.mockResolvedValue(ok(overview));
    renderWithQuery(<ActivityChart />);
    const chart = await screen.findByTestId("activity-chart");
    expect(chart).toHaveTextContent("25 min over the last 30 days · 2 active days");
    const bars = screen.getAllByTestId("activity-bar");
    expect(bars).toHaveLength(30);
    expect(bars[29]).toHaveAttribute("data-active", "true");
    expect(bars[29].getAttribute("title")).toMatch(/^Today: 25 min studied, 4 questions, 2 concepts/);
    expect(bars[27]).toHaveAttribute("data-active", "true"); // questions without minutes
    expect(bars[0]).toHaveAttribute("data-active", "false");
  });

  it("lists open misconceptions", async () => {
    const item = (id: string, status: "active" | "recurring"): StudentMisconception => ({
      id,
      misconception: { id: `m${id}`, name: `Mix-up ${id}`, description: "d", concept_id: "c1", concept_name: "Limits" },
      status,
      occurrence_count: 3,
      detected_at: null,
      resolved_at: null,
      evidence: [],
    });
    fetchMock.mockImplementation(async (u) =>
      String(u).includes("status=recurring") ? page([item("2", "recurring")]) : page([item("1", "active")]),
    );
    renderWithQuery(<MisconceptionsCard />);
    const card = await screen.findByTestId("misconceptions-card");
    await waitFor(() => expect(card).toHaveTextContent("2 to work on"));
    const rows = within(card).getAllByRole("listitem");
    expect(rows[0]).toHaveTextContent("Mix-up 2");
    expect(rows[0]).toHaveTextContent("Back again");
    expect(rows[1]).toHaveTextContent("×3");
  });
});

const profile = (state: Profile["onboarding_state"]): Profile => ({
  id: "u1",
  email: "a@b.c",
  display_name: "Ada",
  avatar_url: null,
  grade_level: null,
  difficulty_band: null,
  language: null,
  timezone: null,
  preferences: {},
  cumulative_stats: {},
  onboarding_state: state,
});

describe("Onboarding (AC-7.4)", () => {
  it("derives the furthest state", () => {
    expect(derivedState({ profile: false, upload: true, session: true })).toBe("new");
    expect(derivedState({ profile: true, upload: false, session: false })).toBe("profile_set");
    expect(derivedState({ profile: true, upload: true, session: false })).toBe("first_upload");
    expect(derivedState({ profile: true, upload: true, session: true })).toBe("first_session");
  });

  function route(state: Profile["onboarding_state"], opts: { docs?: number; ready?: number; studied?: boolean; goals?: number }) {
    fetchMock.mockImplementation(async (u, init) => {
      const url = String(u);
      if (init?.method === "PATCH") return ok(profile(JSON.parse(String(init.body)).onboarding_state));
      if (url.endsWith("/profile")) return ok(profile(state));
      if (url.includes("/documents") && url.includes("status=ready")) return page([], opts.ready ?? 0);
      if (url.includes("/documents")) return page([], opts.docs ?? 0);
      if (url.includes("/sessions")) {
        return page(opts.studied ? [{ id: "s", status: "completed", interaction_count: 3, concepts: [], summary: null }] : []);
      }
      if (url.includes("/goals")) return page([], opts.goals ?? 0);
      return page([]);
    });
  }

  it("guides step by step and advances the stored state", async () => {
    route("profile_set", { docs: 1, ready: 0 });
    renderWithQuery(<OnboardingChecklist />);
    const card = await screen.findByTestId("onboarding");
    await waitFor(() => expect(card).toHaveTextContent("2 of 4 steps done"));
    expect(screen.getByTestId("onboarding-step-profile")).toHaveAttribute("data-done", "true");
    expect(screen.getByTestId("onboarding-step-upload")).toHaveAttribute("data-done", "true");
    expect(screen.getByTestId("onboarding-step-ready")).toHaveTextContent("Processing…");
    // The next actionable step is highlighted with a primary link.
    expect(within(screen.getByTestId("onboarding-step-session")).getByRole("link", { name: "Start studying" })).toHaveAttribute("href", "/session");
    await waitFor(() =>
      expect(fetchMock.mock.calls.some(([, i]) => i?.method === "PATCH" && String(i.body).includes("first_upload"))).toBe(true),
    );
  });

  it("celebrates completion and can be dismissed", async () => {
    route("first_session", { docs: 2, ready: 2, studied: true, goals: 1 });
    renderWithQuery(<OnboardingChecklist />);
    expect(await screen.findByText("You're all set 🎉")).toBeInTheDocument();
    await userEvent.click(screen.getByRole("button", { name: "Done" }));
    await waitFor(() => expect(screen.queryByTestId("onboarding")).toBeNull());
  });

  it("stays hidden once complete", async () => {
    route("complete", {});
    renderWithQuery(<OnboardingChecklist />);
    await waitFor(() => expect(fetchMock).toHaveBeenCalled());
    expect(screen.queryByTestId("onboarding")).toBeNull();
  });
});

describe("Progress page", () => {
  const heatmap: Heatmap = {
    columns: ["2026-09-21", "2026-09-28", TODAY],
    concepts: [
      {
        id: "c1",
        name: "Limits",
        subject_id: null,
        subject_name: null,
        mastery_level: 0.85,
        label: "mastered",
        attempt_count: 4,
        last_assessed_at: null,
        next_review_at: null,
        cells: [null, 0.3, 0.85],
      },
    ],
    total_concepts: 5,
    truncated: true,
  };

  it("renders the heatmap with filters", async () => {
    fetchMock.mockImplementation(async (u) =>
      String(u).includes("/subjects") ? page([{ id: "s1", name: "Maths" }]) : ok(heatmap),
    );
    renderWithQuery(<MasteryHeatmap />);
    const cells = await screen.findAllByTestId("heatmap-cell");
    expect(cells).toHaveLength(3);
    expect(cells[0]).toHaveClass("bg-slate-100");
    expect(cells[1]).toHaveClass("bg-amber-400");
    expect(cells[2]).toHaveClass("bg-emerald-600");
    expect(cells[2]).toHaveAttribute("title", "Limits, Now: 85%");
    expect(screen.getByText(/Showing 1 of 5 concepts/)).toBeInTheDocument();
    await userEvent.selectOptions(screen.getByLabelText("Period"), "12");
    await userEvent.selectOptions(await screen.findByLabelText("Subject"), "s1");
    await waitFor(() => expect(fetchMock.mock.calls.map(urlOf).some((u) => u.includes("subject_id=s1") && u.includes("weeks=12"))).toBe(true));
  });

  it("filters misconceptions and reveals evidence", async () => {
    const item: StudentMisconception = {
      id: "x",
      misconception: { id: "m", name: "Sign error", description: "Flips signs", concept_id: "c1", concept_name: "Limits" },
      status: "resolved",
      occurrence_count: 2,
      detected_at: "2026-09-01T00:00:00Z",
      resolved_at: "2026-09-05T00:00:00Z",
      evidence: [{ attempt_id: null, description: "Wrote -2x", detected_at: "2026-09-01T00:00:00Z" }],
    };
    fetchMock.mockImplementation(async (u) => (String(u).includes("status=active") ? page([]) : page([item])));
    renderWithQuery(<MisconceptionList />);
    const row = await screen.findByTestId("misconception-item");
    expect(row).toHaveTextContent("Resolved");
    expect(row).toHaveTextContent("seen 2×");
    await userEvent.click(within(row).getByRole("button", { name: "Evidence" }));
    expect(row).toHaveTextContent("Wrote -2x");
    await userEvent.click(screen.getByRole("tab", { name: "Active" }));
    expect(await screen.findByText("Nothing here.")).toBeInTheDocument();
  });

  it("shows AI usage against the budget", async () => {
    const usage = (period: string, cost: number): AIUsage => ({
      period: period as AIUsage["period"],
      since: TODAY,
      total_cost_usd: cost,
      today_cost_usd: 4.5,
      daily_budget_usd: 5,
      total_tokens: 12000,
      total_interactions: 7,
      failed_interactions: 1,
      by_purpose: { generate_question: { cost: 0.004, count: 5, tokens: 1000, failed: 1 }, custom_thing: { cost: 1, count: 2, tokens: 9, failed: 0 } },
      by_model: {},
      daily: [],
    });
    fetchMock.mockImplementation(async (u) => ok(usage(String(u).includes("week") ? "week" : "today", String(u).includes("week") ? 3.21 : 0.42)));
    renderWithQuery(<AIUsageCard />);
    expect(await screen.findByTestId("ai-cost")).toHaveTextContent("$0.42");
    expect(screen.getByRole("progressbar", { name: /budget/ })).toHaveAttribute("aria-valuenow", "90");
    expect(screen.getByText("Questions")).toBeInTheDocument();
    expect(screen.getByText("(1 failed)")).toBeInTheDocument();
    expect(screen.getByText("Custom thing")).toBeInTheDocument();
    expect(screen.getByText("<$0.01")).toBeInTheDocument();
    await userEvent.click(screen.getByRole("tab", { name: "7 days" }));
    await waitFor(() => expect(screen.getByTestId("ai-cost")).toHaveTextContent("$3.21"));
  });
});

describe("Concept graph (AC-7.2)", () => {
  const graph: ConceptGraph = {
    nodes: [
      { id: "t", name: "Derivatives", mastery: 0.5, is_target: true },
      { id: "p", name: "Limits", mastery: 0.9, is_target: false },
      { id: "pp", name: "Functions", mastery: 0.1, is_target: false },
      { id: "d", name: "Chain Rule", mastery: 0.3, is_target: false },
      { id: "r", name: "Rates of change", mastery: 0.65, is_target: false },
    ],
    edges: [
      { source: "p", target: "t", type: "prerequisite" },
      { source: "pp", target: "p", type: "prerequisite" },
      { source: "t", target: "d", type: "prerequisite" },
      { source: "t", target: "r", type: "related" },
    ],
  };

  it("lays out prerequisites left, dependents right", () => {
    const { nodes, width } = layoutGraph(graph, "t");
    const x = Object.fromEntries(nodes.map((n) => [n.id, n.x]));
    expect(x.pp).toBeLessThan(x.p);
    expect(x.p).toBeLessThan(x.t);
    expect(x.t).toBeLessThan(x.d);
    expect(x.r).toBe(x.t); // related: same column
    expect(width).toBeGreaterThan(x.d);
  });

  it("colours by mastery and navigates on click / Enter", async () => {
    fetchMock.mockImplementation(async () => ok(graph));
    renderWithQuery(<ConceptGraphView conceptId="t" />);
    const nodes = await screen.findAllByTestId("graph-node");
    expect(nodes).toHaveLength(5);
    expect(screen.getAllByTestId("graph-edge")).toHaveLength(4);
    const limits = screen.getByRole("link", { name: /Limits, mastery 90%/ });
    expect(limits.querySelector("rect")).toHaveClass("fill-emerald-600");
    await userEvent.click(limits);
    expect(push).toHaveBeenCalledWith("/concepts/p");
    screen.getByRole("link", { name: /Chain Rule/ }).focus();
    await userEvent.keyboard("{Enter}");
    expect(push).toHaveBeenCalledWith("/concepts/d");
    await userEvent.click(screen.getByRole("img", { name: /Derivatives.*this concept/ }));
    expect(push).toHaveBeenCalledTimes(2); // the current concept is not a link
    await userEvent.selectOptions(screen.getByLabelText("Depth"), "2");
    await waitFor(() => expect(fetchMock.mock.calls.map(urlOf).some((u) => u.includes("depth=2"))).toBe(true));
  });

  it("explains an isolated concept", async () => {
    fetchMock.mockImplementation(async () => ok({ nodes: [graph.nodes[0]], edges: [] }));
    renderWithQuery(<ConceptGraphView conceptId="t" />);
    expect(await screen.findByText("No linked concepts yet.")).toBeInTheDocument();
  });
});

describe("Notifications wiring", () => {
  it("mutation meta drives success and error toasts", async () => {
    function Probe() {
      const good = useMutation({ mutationFn: async () => 1, meta: { success: "Saved!" } });
      const bad = useMutation({
        mutationFn: async () => {
          throw new Error("Nope");
        },
        meta: { errorToast: true },
      });
      return (
        <>
          <button onClick={() => good.mutate()}>good</button>
          <button onClick={() => bad.mutate()}>bad</button>
        </>
      );
    }
    render(
      <Providers>
        <Probe />
      </Providers>,
    );
    await userEvent.click(screen.getByRole("button", { name: "good" }));
    expect(await screen.findByText("Saved!")).toBeInTheDocument();
    await userEvent.click(screen.getByRole("button", { name: "bad" }));
    expect(await screen.findByText("Nope")).toBeInTheDocument();
  });

  it("toasts when a watched document finishes processing", () => {
    const doc = (status: DocumentSummary["processing_status"]) =>
      ({ id: "d1", title: "Notes", processing_status: status }) as DocumentSummary;
    const client = new QueryClient();
    const { rerender } = renderHook(({ docs }) => useProcessingToasts(docs), {
      initialProps: { docs: [doc("processing")] },
      wrapper: ({ children }) => <QueryClientProvider client={client}>{children}</QueryClientProvider>,
    });
    act(() => rerender({ docs: [doc("ready")] }));
    act(() => rerender({ docs: [{ ...doc("failed"), id: "d2" }] }));
    act(() => rerender({ docs: [{ ...doc("processing"), id: "d3" }] }));
    act(() => rerender({ docs: [{ ...doc("failed"), id: "d3", title: "Scan" }] }));
    expect(useToastStore.getState().toasts.map((t) => [t.tone, t.message])).toEqual([
      ["success", "“Notes” is ready to study"],
      ["error", "“Scan” could not be processed"],
    ]);
  });
});

describe("Concept graph robustness", () => {
  it("lays out a malformed payload as an empty graph instead of throwing", () => {
    const { nodes } = layoutGraph({} as ConceptGraph, "t");
    expect(nodes).toEqual([]);
  });
});
