import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { DocumentList } from "@/components/upload/document-list";
import { UploadPanel } from "@/components/upload/upload-panel";
import type { DocumentSummary } from "@/types/domain";

import { jsonResponse, meta, renderWithQuery } from "./test-utils";

vi.mock("next/link", () => ({
  default: (props: React.AnchorHTMLAttributes<HTMLAnchorElement>) => <a {...props} />,
}));

const upload = vi.hoisted(() => vi.fn());
vi.mock("@/lib/upload", async (importOriginal) => ({
  ...(await importOriginal<typeof import("@/lib/upload")>()),
  uploadDocument: upload,
}));

const fetchMock = vi.fn<typeof fetch>();
const page = (data: unknown[]) => ({
  data,
  meta: { ...meta, pagination: { total: data.length, page: 1, per_page: 20, total_pages: data.length ? 1 : 0 } },
});

function doc(overrides: Partial<DocumentSummary>): DocumentSummary {
  return {
    id: "d1",
    title: "Chapter 5",
    source_filename: "ch5.pdf",
    mime_type: "application/pdf",
    file_size_bytes: 2048,
    processing_status: "ready",
    processing_metadata: {},
    subject_id: null,
    course_id: null,
    uploaded_at: "2026-10-04T10:00:00Z",
    processed_at: null,
    ...overrides,
  };
}

beforeEach(() => vi.stubGlobal("fetch", fetchMock));
afterEach(() => {
  vi.unstubAllGlobals();
  fetchMock.mockReset();
  upload.mockReset();
});

describe("UploadPanel", () => {
  beforeEach(() => {
    fetchMock.mockImplementation(async () =>
      jsonResponse(200, page([{ id: "s1", name: "Physics", course_count: 0, concept_count: 0, avg_mastery: 0 }])),
    );
  });

  it("uploads chosen files with the selected subject and shows progress", async () => {
    let finish: () => void = () => {};
    upload.mockImplementation(
      (_file: File, opts: { onProgress: (p: number) => void }) =>
        new Promise((resolve) => {
          opts.onProgress(0.4);
          finish = () => resolve({ document_id: "d1", processing_status: "processing", task_id: "t" });
        }),
    );
    renderWithQuery(<UploadPanel />);
    const user = userEvent.setup();
    await user.selectOptions(await screen.findByLabelText("Subject (optional)"), await screen.findByRole("option", { name: "Physics" }));

    await user.upload(screen.getByLabelText("Choose files to upload"), new File(["%PDF"], "notes.pdf"));

    const item = await screen.findByTestId("upload-item");
    expect(within(item).getByText("notes.pdf")).toBeInTheDocument();
    expect(within(item).getByRole("progressbar")).toHaveAttribute("aria-valuenow", "40");
    expect(upload.mock.calls[0][1].subjectId).toBe("s1");
    finish();
    expect(await within(item).findByText(/Uploaded — processing/)).toBeInTheDocument();
  });

  it("rejects unsupported files without uploading", async () => {
    renderWithQuery(<UploadPanel />);
    const user = userEvent.setup({ applyAccept: false });
    await user.upload(screen.getByLabelText("Choose files to upload"), new File(["x"], "virus.exe"));
    expect(await screen.findByRole("alert")).toHaveTextContent("Unsupported file type");
    expect(upload).not.toHaveBeenCalled();
  });

  it("shows API errors such as the upload rate limit", async () => {
    const { ApiError } = await import("@/lib/api");
    upload.mockRejectedValue(new ApiError(429, "RATE_LIMITED", "Too many"));
    renderWithQuery(<UploadPanel />);
    await userEvent.setup().upload(screen.getByLabelText("Choose files to upload"), new File(["x"], "a.txt"));
    expect(await screen.findByRole("alert")).toHaveTextContent("Upload limit reached");
  });

  it("accepts dropped files", async () => {
    upload.mockResolvedValue({ document_id: "d", processing_status: "processing", task_id: null });
    renderWithQuery(<UploadPanel />);
    const zone = screen.getByTestId("dropzone");
    const file = new File(["hello"], "dropped.txt");
    const dataTransfer = { files: [file] } as unknown as DataTransfer;
    zone.dispatchEvent(Object.assign(new Event("dragover", { bubbles: true }), { dataTransfer }));
    zone.dispatchEvent(Object.assign(new Event("drop", { bubbles: true, cancelable: true }), { dataTransfer }));
    await waitFor(() => expect(upload).toHaveBeenCalledWith(file, expect.anything()));
  });
});

describe("DocumentList", () => {
  it("shows status, progress, stats, errors and warnings", async () => {
    fetchMock.mockResolvedValue(
      jsonResponse(
        200,
        page([
          doc({ id: "a", title: "Ready doc", processing_metadata: { page_count: 3, chunk_count: 5, concept_count: 4 } }),
          doc({
            id: "b",
            title: "Busy doc",
            processing_status: "processing",
            processing_metadata: { stage: "extracting_concepts", progress: 0.3 },
          }),
          doc({
            id: "c",
            title: "Broken doc",
            processing_status: "failed",
            processing_metadata: { error: { code: "NO_TEXT", message: "No readable text" }, warnings: ["2 page(s) had no text layer"] },
          }),
        ]),
      ),
    );
    renderWithQuery(<DocumentList />);

    const rows = await screen.findAllByTestId("document-row");
    expect(rows.map((r) => r.dataset.status)).toEqual(["ready", "processing", "failed"]);
    expect(within(rows[0]).getByTestId("document-stats")).toHaveTextContent("3 pages · 5 sections · 4 concepts");
    expect(within(rows[0]).getByRole("link", { name: /View concepts/ })).toHaveAttribute("href", "/concepts?document_id=a");
    expect(within(rows[1]).getByText("Finding concepts · 30%")).toBeInTheDocument();
    expect(within(rows[1]).getByRole("progressbar")).toHaveAttribute("aria-valuenow", "30");
    expect(within(rows[2]).getByRole("alert")).toHaveTextContent("No readable text");
    expect(within(rows[2]).getByText("2 page(s) had no text layer")).toBeInTheDocument();
  });

  it("retries a failed document and deletes after confirmation", async () => {
    fetchMock.mockImplementation(async (input, init) => {
      const url = String(input);
      if (init?.method === "POST") return jsonResponse(202, { data: { document_id: "c", processing_status: "processing", task_id: "t" }, meta });
      if (init?.method === "DELETE") return new Response(null, { status: 204 });
      return jsonResponse(200, page([doc({ id: "c", processing_status: "failed", processing_metadata: {} })]));
      void url;
    });
    renderWithQuery(<DocumentList />);
    const user = userEvent.setup();

    await user.click(await screen.findByRole("button", { name: "Retry" }));
    await waitFor(() =>
      expect(fetchMock.mock.calls.some(([u, i]) => u === "/api/backend/documents/c/confirm-upload" && i?.method === "POST")).toBe(true),
    );

    await user.click(screen.getByRole("button", { name: "Delete" }));
    expect(screen.getByText(/Delete this document/)).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "Delete" }));
    await waitFor(() =>
      expect(fetchMock.mock.calls.some(([u, i]) => u === "/api/backend/documents/c" && i?.method === "DELETE")).toBe(true),
    );
  });

  it("shows the empty state", async () => {
    fetchMock.mockResolvedValue(jsonResponse(200, page([])));
    renderWithQuery(<DocumentList />);
    expect(await screen.findByText(/No documents yet/)).toBeInTheDocument();
  });
});
