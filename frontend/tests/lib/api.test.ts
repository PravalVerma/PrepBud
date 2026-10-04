import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { api, ApiError } from "@/lib/api";

const fetchMock = vi.fn<typeof fetch>();

function json(status: number, body: unknown, headers: Record<string, string> = {}): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "Content-Type": "application/json", ...headers },
  });
}

const meta = { request_id: "rid-1", timestamp: "2026-10-04T00:00:00Z" };

beforeEach(() => {
  vi.stubGlobal("fetch", fetchMock);
});
afterEach(() => {
  vi.unstubAllGlobals();
  fetchMock.mockReset();
});

describe("api client", () => {
  it("calls the same-origin backend proxy and unwraps the envelope", async () => {
    fetchMock.mockResolvedValue(json(200, { data: { id: "p1", email: "a@b.co" }, meta }));

    const profile = await api.getProfile();

    expect(profile).toEqual({ id: "p1", email: "a@b.co" });
    const [url, init] = fetchMock.mock.calls[0];
    expect(url).toBe("/api/backend/profile");
    expect(init?.method).toBe("GET");
    expect(init?.credentials).toBe("same-origin");
    expect(init?.body).toBeUndefined();
  });

  it("sends JSON bodies for mutations", async () => {
    fetchMock.mockResolvedValue(json(200, { data: { timezone: "Asia/Kolkata" }, meta }));

    await api.updateProfile({ timezone: "Asia/Kolkata" });

    const [, init] = fetchMock.mock.calls[0];
    expect(init?.method).toBe("PATCH");
    expect(init?.body).toBe(JSON.stringify({ timezone: "Asia/Kolkata" }));
    expect((init?.headers as Record<string, string>)["Content-Type"]).toBe("application/json");
  });

  it("keeps pagination meta for list endpoints", async () => {
    const pagination = { total: 1, page: 2, per_page: 5, total_pages: 1 };
    fetchMock.mockResolvedValue(json(200, { data: [{ id: "s1" }], meta: { ...meta, pagination } }));

    const result = await api.listSubjects({ page: 2, per_page: 5 });

    expect(fetchMock.mock.calls[0][0]).toBe("/api/backend/subjects?page=2&per_page=5");
    expect(result.meta.pagination).toEqual(pagination);
  });

  it("returns undefined for 204 No Content", async () => {
    fetchMock.mockResolvedValue(new Response(null, { status: 204 }));
    await expect(api.deleteSubject("s1")).resolves.toBeUndefined();
  });

  it("raises ApiError with the contract error fields", async () => {
    fetchMock.mockResolvedValue(
      json(409, {
        error: {
          code: "CONFLICT",
          message: "A subject with this name already exists",
          details: { field: "name" },
        },
        meta,
      }),
    );

    const err = await api.createSubject({ name: "Maths" }).catch((e: unknown) => e);

    expect(err).toBeInstanceOf(ApiError);
    const apiErr = err as ApiError;
    expect(apiErr.status).toBe(409);
    expect(apiErr.code).toBe("CONFLICT");
    expect(apiErr.requestId).toBe("rid-1");
    expect(apiErr.fieldErrors).toEqual({ name: "A subject with this name already exists" });
  });

  it("maps validation errors to fields", async () => {
    fetchMock.mockResolvedValue(
      json(400, {
        error: {
          code: "VALIDATION_ERROR",
          message: "Invalid request body or parameters",
          details: {
            errors: [
              {
                loc: ["body", "timezone"],
                message: "Value error, must be a valid IANA timezone",
                type: "value_error",
              },
            ],
          },
        },
        meta,
      }),
    );

    const err = (await api.updateProfile({ timezone: "Mars/Base" }).catch((e) => e)) as ApiError;

    expect(err.fieldErrors).toEqual({ timezone: "must be a valid IANA timezone" });
  });

  it("handles non-JSON error bodies", async () => {
    fetchMock.mockResolvedValue(new Response("Bad gateway", { status: 502 }));
    const err = (await api.getProfile().catch((e) => e)) as ApiError;
    expect(err.status).toBe(502);
    expect(err.code).toBe("INTERNAL_ERROR");
  });

  it("wraps network failures", async () => {
    fetchMock.mockRejectedValue(new TypeError("Failed to fetch"));
    const err = (await api.getProfile().catch((e) => e)) as ApiError;
    expect(err.code).toBe("NETWORK_ERROR");
    expect(err.status).toBe(0);
  });

  it("builds nested curriculum paths", async () => {
    fetchMock.mockImplementation(async () => json(201, { data: { id: "c1" }, meta }));
    await api.createCourse("s1", { name: "Algebra" });
    await api.createChapter("c1", { name: "Quadratics" });
    await api.createSection("ch1", { name: "Formula" });
    expect(fetchMock.mock.calls.map((c) => c[0])).toEqual([
      "/api/backend/subjects/s1/courses",
      "/api/backend/courses/c1/chapters",
      "/api/backend/chapters/ch1/sections",
    ]);
  });
});
