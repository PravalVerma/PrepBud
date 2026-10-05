import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { api, query } from "@/lib/api";
import {
  ACCEPT_ATTRIBUTE,
  checkFile,
  formatBytes,
  MAX_UPLOAD_BYTES,
  putWithProgress,
  uploadDocument,
  UploadError,
} from "@/lib/upload";

/** Minimal XMLHttpRequest stand-in that lets a test drive progress and completion. */
class FakeXHR {
  static last: FakeXHR | null = null;
  method = "";
  url = "";
  headers: Record<string, string> = {};
  body: unknown = null;
  status = 0;
  upload: { onprogress: ((e: { lengthComputable: boolean; loaded: number; total: number }) => void) | null } = {
    onprogress: null,
  };
  onload: (() => void) | null = null;
  onerror: (() => void) | null = null;
  onabort: (() => void) | null = null;

  constructor() {
    FakeXHR.last = this;
  }
  open(method: string, url: string) {
    this.method = method;
    this.url = url;
  }
  setRequestHeader(name: string, value: string) {
    this.headers[name] = value;
  }
  send(body: unknown) {
    this.body = body;
  }
  abort() {
    this.onabort?.();
  }
  progress(loaded: number, total: number) {
    this.upload.onprogress?.({ lengthComputable: true, loaded, total });
  }
  finish(status: number) {
    this.status = status;
    this.onload?.();
  }
}

beforeEach(() => {
  FakeXHR.last = null;
  vi.stubGlobal("XMLHttpRequest", FakeXHR);
});
afterEach(() => {
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
});

describe("checkFile", () => {
  it.each([
    ["notes.pdf", "application/pdf"],
    ["NOTES.TXT", "text/plain"],
    ["board.png", "image/png"],
    ["photo.jpg", "image/jpeg"],
    ["photo.JPEG", "image/jpeg"],
  ])("accepts %s as %s", (name, mime) => {
    expect(checkFile({ name, size: 10 })).toEqual({ ok: true, mimeType: mime });
  });

  it.each([
    [{ name: "virus.exe", size: 10 }, /Unsupported/],
    [{ name: "noextension", size: 10 }, /Unsupported/],
    [{ name: "empty.pdf", size: 0 }, /empty/],
    [{ name: "huge.pdf", size: MAX_UPLOAD_BYTES + 1 }, /50 MB/],
  ])("rejects %o", (file, message) => {
    const result = checkFile(file);
    expect(result.ok).toBe(false);
    if (!result.ok) expect(result.error).toMatch(message);
  });

  it("exposes the accept attribute", () => {
    expect(ACCEPT_ATTRIBUTE).toBe(".pdf,.txt,.png,.jpg,.jpeg");
  });
});

describe("putWithProgress", () => {
  it("PUTs with the signed headers and reports progress", async () => {
    const progress: number[] = [];
    const done = putWithProgress("https://s3/x", new Blob(["hi"]), { "Content-Type": "text/plain" }, (p) =>
      progress.push(p),
    );
    const xhr = FakeXHR.last!;
    expect(xhr.method).toBe("PUT");
    expect(xhr.url).toBe("https://s3/x");
    expect(xhr.headers).toEqual({ "Content-Type": "text/plain" });
    xhr.progress(1, 2);
    xhr.finish(200);
    await done;
    expect(progress).toEqual([0.5, 1]);
  });

  it("rejects on HTTP errors, network errors and aborts", async () => {
    const forbidden = putWithProgress("u", new Blob(), {}, () => {});
    FakeXHR.last!.finish(403);
    await expect(forbidden).rejects.toThrow("HTTP 403");

    const network = putWithProgress("u", new Blob(), {}, () => {});
    FakeXHR.last!.onerror?.();
    await expect(network).rejects.toBeInstanceOf(UploadError);

    const controller = new AbortController();
    const aborted = putWithProgress("u", new Blob(), {}, () => {}, controller.signal);
    controller.abort();
    await expect(aborted).rejects.toThrow("cancelled");
  });
});

describe("uploadDocument", () => {
  it("requests a URL, uploads directly to storage, then confirms", async () => {
    const requestUploadUrl = vi.spyOn(api, "requestUploadUrl").mockResolvedValue({
      upload_url: "https://s3/presigned",
      document_id: "d1",
      s3_key: "k",
      expires_in_seconds: 3600,
      upload_headers: { "Content-Type": "application/pdf" },
    });
    const confirm = vi
      .spyOn(api, "confirmUpload")
      .mockResolvedValue({ document_id: "d1", processing_status: "processing", task_id: "t1" });
    const file = new File(["%PDF-1.4"], "ch5.pdf", { type: "" });

    const pending = uploadDocument(file, { subjectId: "s1" });
    await vi.waitFor(() => expect(FakeXHR.last).not.toBeNull());
    FakeXHR.last!.finish(200);
    const result = await pending;

    expect(requestUploadUrl).toHaveBeenCalledWith({
      filename: "ch5.pdf",
      mime_type: "application/pdf",
      file_size_bytes: file.size,
      subject_id: "s1",
    });
    expect(FakeXHR.last!.headers).toEqual({ "Content-Type": "application/pdf" });
    expect(FakeXHR.last!.body).toBe(file);
    expect(confirm).toHaveBeenCalledWith("d1");
    expect(result.processing_status).toBe("processing");
  });

  it("refuses invalid files before calling the API", async () => {
    const spy = vi.spyOn(api, "requestUploadUrl");
    await expect(uploadDocument(new File(["x"], "a.exe"))).rejects.toThrow("Unsupported");
    expect(spy).not.toHaveBeenCalled();
  });
});

describe("helpers", () => {
  it("formats sizes", () => {
    expect(formatBytes(null)).toBe("—");
    expect(formatBytes(512)).toBe("512 B");
    expect(formatBytes(2048)).toBe("2 KB");
    expect(formatBytes(5 * 1024 * 1024)).toBe("5.0 MB");
  });

  it("builds query strings, skipping empty values", () => {
    expect(query()).toBe("");
    expect(query({ page: 2, search: "", subject_id: undefined, q: "a b" })).toBe("?page=2&q=a+b");
  });
});
