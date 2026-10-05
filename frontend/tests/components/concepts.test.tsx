import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { ConceptBrowser, difficultyLabel } from "@/components/concepts/concept-browser";
import { ConceptDetailView, highlight } from "@/components/concepts/concept-detail";
import type { ConceptDetail, ConceptListItem } from "@/types/domain";

import { jsonResponse, meta, renderWithQuery } from "./test-utils";

vi.mock("next/link", () => ({
  default: (props: React.AnchorHTMLAttributes<HTMLAnchorElement>) => <a {...props} />,
}));
vi.mock("next/navigation", () => ({ useRouter: () => ({ push: vi.fn() }) }));

const fetchMock = vi.fn<typeof fetch>();
const page = (data: unknown[], total = data.length, totalPages = data.length ? 1 : 0) => ({
  data,
  meta: { ...meta, pagination: { total, page: 1, per_page: 20, total_pages: totalPages } },
});

const derivatives: ConceptListItem = {
  id: "c1",
  name: "Derivatives",
  description: "Instantaneous rate of change",
  difficulty_estimate: 0.5,
  subject_id: null,
  chapter_id: null,
  mastery: { level: 0.45, label: "intermediate", last_assessed_at: null, next_review_at: null },
  prerequisite_count: 1,
  document_count: 2,
};

beforeEach(() => vi.stubGlobal("fetch", fetchMock));
afterEach(() => {
  vi.unstubAllGlobals();
  fetchMock.mockReset();
});

const calls = () => fetchMock.mock.calls.map(([u]) => String(u));

describe("ConceptBrowser", () => {
  it("lists concepts with mastery and counts", async () => {
    fetchMock.mockImplementation(async (u) =>
      String(u).startsWith("/api/backend/subjects") ? jsonResponse(200, page([])) : jsonResponse(200, page([derivatives])),
    );
    renderWithQuery(<ConceptBrowser />);

    const row = await screen.findByTestId("concept-row");
    expect(row).toHaveAttribute("href", "/concepts/c1");
    expect(row).toHaveTextContent("Derivatives");
    expect(row).toHaveTextContent("Medium · 1 prerequisite · 2 documents");
    expect(row).toHaveTextContent("45% · intermediate");
    expect(screen.getByText("1 concept")).toBeInTheDocument();
  });

  it("searches (debounced) and filters by document", async () => {
    fetchMock.mockImplementation(async (u) =>
      String(u).startsWith("/api/backend/subjects") ? jsonResponse(200, page([])) : jsonResponse(200, page([])),
    );
    renderWithQuery(<ConceptBrowser initialDocumentId="doc-9" />);
    await screen.findByText("No concepts match these filters.");
    expect(calls()).toContain("/api/backend/concepts?page=1&per_page=20&document_id=doc-9");

    const user = userEvent.setup();
    await user.type(screen.getByLabelText("Search concepts"), "chain");
    await waitFor(() =>
      expect(calls()).toContain("/api/backend/concepts?page=1&per_page=20&search=chain&document_id=doc-9"),
    );
    await user.click(screen.getByRole("button", { name: "Show all concepts" }));
    await waitFor(() => expect(calls()).toContain("/api/backend/concepts?page=1&per_page=20&search=chain"));
  });

  it("shows the empty state with an upload link", async () => {
    fetchMock.mockImplementation(async () => jsonResponse(200, page([])));
    renderWithQuery(<ConceptBrowser />);
    expect(await screen.findByRole("link", { name: "Upload study material" })).toHaveAttribute("href", "/upload");
  });

  it("paginates", async () => {
    fetchMock.mockImplementation(async (u) =>
      String(u).startsWith("/api/backend/subjects") ? jsonResponse(200, page([])) : jsonResponse(200, page([derivatives], 30, 2)),
    );
    renderWithQuery(<ConceptBrowser />);
    await userEvent.setup().click(await screen.findByRole("button", { name: "Next" }));
    await waitFor(() => expect(calls()).toContain("/api/backend/concepts?page=2&per_page=20"));
  });
});

const detail: ConceptDetail = {
  id: "c1",
  name: "Derivatives",
  description: "Instantaneous rate of change",
  difficulty_estimate: 0.8,
  subject_id: null,
  chapter_id: null,
  section_id: null,
  mastery: {
    level: 0.45,
    label: "intermediate",
    last_assessed_at: null,
    next_review_at: null,
    confidence: 0.5,
    attempt_count: 8,
    correct_count: 5,
    streak: 2,
    history: [],
  },
  prerequisites: [{ id: "c0", name: "Limits", mastery_level: 0.72 }],
  dependents: [],
  related_concepts: [{ id: "c3", name: "Calculus", relationship: "generalisation" }],
  misconceptions: [{ id: "m1", name: "Sign error", status: "active" }],
  documents: [{ id: "d1", title: "Chapter 2", section_count: 3 }],
  created_at: null,
  metadata: {},
};

describe("ConceptDetailView", () => {
  it("shows the concept, its graph neighbours, documents and matching material", async () => {
    fetchMock.mockImplementation(async (u) => {
      const url = String(u);
      if (url.startsWith("/api/backend/search")) {
        return jsonResponse(
          200,
          page([
            {
              section_id: "s1",
              document_id: "d1",
              document_title: "Chapter 2",
              section_index: 0,
              heading: "2.1 Rates",
              page_numbers: [4, 5],
              snippet: "the **derivative** measures change",
              score: 0.03,
              matched_by: ["keyword", "semantic"],
            },
          ]),
        );
      }
      return jsonResponse(200, { data: detail, meta });
    });
    renderWithQuery(<ConceptDetailView id="c1" />);

    expect(await screen.findByRole("heading", { name: "Derivatives" })).toBeInTheDocument();
    expect(screen.getByText("Difficulty: Hard")).toBeInTheDocument();
    expect(screen.getByText("Mastery 45% · intermediate")).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "Limits" })).toHaveAttribute("href", "/concepts/c0");
    expect(screen.getByText("Nothing depends on this concept yet.")).toBeInTheDocument();
    expect(screen.getByText("More general")).toBeInTheDocument();
    expect(screen.getByText("Sign error")).toBeInTheDocument();
    const hit = await screen.findByTestId("search-hit");
    expect(hit).toHaveTextContent("Chapter 2 · 2.1 Rates · p. 4, 5");
    expect(hit.querySelector("mark")).toHaveTextContent("derivative");
    expect(calls()).toContain("/api/backend/search?q=Derivatives&concept_id=c1&per_page=5");
  });

  it("explains a missing concept", async () => {
    fetchMock.mockResolvedValue(
      jsonResponse(404, { error: { code: "RESOURCE_NOT_FOUND", message: "Concept not found", details: {} }, meta }),
    );
    renderWithQuery(<ConceptDetailView id="nope" />);
    expect(await screen.findByRole("alert")).toHaveTextContent("This concept does not exist.");
  });
});

describe("helpers", () => {
  it("labels difficulty", () => {
    expect([0.2, 0.5, 0.9].map(difficultyLabel)).toEqual(["Easy", "Medium", "Hard"]);
  });

  it("highlights without injecting HTML", () => {
    render(<p data-testid="p">{highlight("a **b** <script>x</script> **c**")}</p>);
    const p = screen.getByTestId("p");
    expect(Array.from(p.querySelectorAll("mark")).map((m) => m.textContent)).toEqual(["b", "c"]);
    expect(p.querySelector("script")).toBeNull();
    expect(p).toHaveTextContent("<script>x</script>");
  });
});
