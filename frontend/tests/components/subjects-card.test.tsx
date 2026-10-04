import { screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { SubjectsCard } from "@/components/dashboard/subjects-card";

import { jsonResponse, meta, renderWithQuery } from "./test-utils";

const fetchMock = vi.fn<typeof fetch>();
const pagination = (total: number) => ({ total, page: 1, per_page: 20, total_pages: total ? 1 : 0 });

const maths = {
  id: "s1",
  name: "Mathematics",
  description: null,
  icon: "📐",
  course_count: 2,
  concept_count: 1,
  avg_mastery: 0.62,
  created_at: null,
  updated_at: null,
};

beforeEach(() => vi.stubGlobal("fetch", fetchMock));
afterEach(() => {
  vi.unstubAllGlobals();
  fetchMock.mockReset();
});

describe("SubjectsCard", () => {
  it("lists the user's subjects with stats", async () => {
    fetchMock.mockResolvedValue(jsonResponse(200, { data: [maths], meta: { ...meta, pagination: pagination(1) } }));

    renderWithQuery(<SubjectsCard />);

    expect(await screen.findByText("Mathematics")).toBeInTheDocument();
    expect(screen.getByText("2 courses · 1 concept")).toBeInTheDocument();
    expect(screen.getByText("62% · proficient")).toBeInTheDocument();
  });

  it("shows an empty state", async () => {
    fetchMock.mockResolvedValue(jsonResponse(200, { data: [], meta: { ...meta, pagination: pagination(0) } }));
    renderWithQuery(<SubjectsCard />);
    expect(await screen.findByText(/No subjects yet/)).toBeInTheDocument();
  });

  it("shows a load error", async () => {
    fetchMock.mockResolvedValue(
      jsonResponse(503, { error: { code: "SERVICE_UNAVAILABLE", message: "Down", details: {} }, meta }),
    );
    renderWithQuery(<SubjectsCard />);
    expect(await screen.findByRole("alert")).toHaveTextContent("Could not load subjects. Down");
  });

  it("creates a subject and refreshes the list", async () => {
    fetchMock
      .mockResolvedValueOnce(jsonResponse(200, { data: [], meta: { ...meta, pagination: pagination(0) } }))
      .mockResolvedValueOnce(jsonResponse(201, { data: maths, meta }))
      .mockResolvedValueOnce(jsonResponse(200, { data: [maths], meta: { ...meta, pagination: pagination(1) } }));
    renderWithQuery(<SubjectsCard />);
    const user = userEvent.setup();

    await user.click(await screen.findByRole("button", { name: "Add subject" }));
    await user.type(screen.getByLabelText("Subject name"), "Mathematics");
    await user.click(screen.getByRole("button", { name: "Save" }));

    expect(await screen.findByText("Mathematics")).toBeInTheDocument();
    const [url, init] = fetchMock.mock.calls[1];
    expect(url).toBe("/api/backend/subjects");
    expect(init?.body).toBe(JSON.stringify({ name: "Mathematics" }));
  });

  it("shows a duplicate-name conflict on the field", async () => {
    fetchMock
      .mockResolvedValueOnce(jsonResponse(200, { data: [maths], meta: { ...meta, pagination: pagination(1) } }))
      .mockResolvedValueOnce(
        jsonResponse(409, {
          error: { code: "CONFLICT", message: "A subject with this name already exists", details: { field: "name" } },
          meta,
        }),
      );
    renderWithQuery(<SubjectsCard />);
    const user = userEvent.setup();

    await user.click(await screen.findByRole("button", { name: "Add subject" }));
    await user.type(screen.getByLabelText("Subject name"), "Mathematics");
    await user.click(screen.getByRole("button", { name: "Save" }));

    await waitFor(() =>
      expect(screen.getByText("A subject with this name already exists")).toBeInTheDocument(),
    );
  });
});
