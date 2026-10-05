import { describe, expect, it, vi } from "vitest";

import { buildBackendUrl, forwardToBackend, timeoutFor } from "@/lib/backend";

const BASE = "http://api.local/api/v1";

function upstream(status: number, body: unknown, headers: Record<string, string> = {}) {
  return vi.fn<typeof fetch>().mockResolvedValue(
    new Response(body === null ? null : JSON.stringify(body), {
      status,
      headers: { "Content-Type": "application/json", ...headers },
    }),
  );
}

describe("buildBackendUrl", () => {
  it("joins path segments and keeps the query string", () => {
    expect(buildBackendUrl(`${BASE}/`, ["subjects", "abc", "courses"], "?page=2")).toBe(
      `${BASE}/subjects/abc/courses?page=2`,
    );
  });

  it.each([[[]], [[".."]], [["subjects", "."]], [["a b"]], [["%2e%2e"]], [["x?y"]]])(
    "rejects unsafe segments %j",
    (path) => {
      expect(buildBackendUrl(BASE, path, "")).toBeNull();
    },
  );
});

describe("forwardToBackend", () => {
  it("answers 401 without calling the backend when signed out", async () => {
    const fetchImpl = vi.fn<typeof fetch>();
    const res = await forwardToBackend({
      request: new Request("http://web/api/backend/profile"),
      path: ["profile"],
      token: null,
      baseUrl: BASE,
      fetchImpl,
    });

    expect(res.status).toBe(401);
    expect((await res.json()).error.code).toBe("AUTHENTICATION_REQUIRED");
    expect(fetchImpl).not.toHaveBeenCalled();
  });

  it("attaches the bearer token and relays status, body and selected headers", async () => {
    const fetchImpl = upstream(
      201,
      { data: { id: "s1" }, meta: {} },
      { "X-Request-ID": "rid-9", "X-RateLimit-Remaining": "99", "Set-Cookie": "leak=1" },
    );
    const request = new Request("http://web/api/backend/subjects?x=1", {
      method: "POST",
      headers: { "Content-Type": "application/json", Cookie: "sb-auth=secret" },
      body: JSON.stringify({ name: "Maths" }),
    });

    const res = await forwardToBackend({
      request,
      path: ["subjects"],
      token: "jwt-123",
      baseUrl: BASE,
      fetchImpl,
    });

    const [url, init] = fetchImpl.mock.calls[0];
    const sent = new Headers(init?.headers);
    expect(url).toBe(`${BASE}/subjects?x=1`);
    expect(init?.method).toBe("POST");
    expect(sent.get("Authorization")).toBe("Bearer jwt-123");
    expect(sent.get("Content-Type")).toBe("application/json");
    expect(sent.get("Cookie")).toBeNull(); // browser cookies never reach the API
    expect(new TextDecoder().decode(init?.body as ArrayBuffer)).toBe('{"name":"Maths"}');

    expect(res.status).toBe(201);
    expect(await res.json()).toEqual({ data: { id: "s1" }, meta: {} });
    expect(res.headers.get("X-Request-ID")).toBe("rid-9");
    expect(res.headers.get("X-RateLimit-Remaining")).toBe("99");
    expect(res.headers.get("Set-Cookie")).toBeNull();
    expect(res.headers.get("Cache-Control")).toBe("no-store");
  });

  it("sends no body for GET", async () => {
    const fetchImpl = upstream(200, { data: [], meta: {} });
    await forwardToBackend({
      request: new Request("http://web/api/backend/subjects"),
      path: ["subjects"],
      token: "t",
      baseUrl: BASE,
      fetchImpl,
    });
    expect(fetchImpl.mock.calls[0][1]?.body).toBeUndefined();
  });

  it("relays 204 with an empty body", async () => {
    const fetchImpl = upstream(204, null);
    const res = await forwardToBackend({
      request: new Request("http://web/api/backend/subjects/1", { method: "DELETE" }),
      path: ["subjects", "1"],
      token: "t",
      baseUrl: BASE,
      fetchImpl,
    });
    expect(res.status).toBe(204);
    expect(await res.text()).toBe("");
  });

  it("passes backend errors through untouched", async () => {
    const body = { error: { code: "RESOURCE_NOT_FOUND", message: "Subject not found", details: {} } };
    const res = await forwardToBackend({
      request: new Request("http://web/api/backend/subjects/1"),
      path: ["subjects", "1"],
      token: "t",
      baseUrl: BASE,
      fetchImpl: upstream(404, body),
    });
    expect(res.status).toBe(404);
    expect(await res.json()).toEqual(body);
  });

  it("returns 503 when the backend is unreachable", async () => {
    const fetchImpl = vi.fn<typeof fetch>().mockRejectedValue(new TypeError("ECONNREFUSED"));
    const res = await forwardToBackend({
      request: new Request("http://web/api/backend/profile"),
      path: ["profile"],
      token: "t",
      baseUrl: BASE,
      fetchImpl,
    });
    expect(res.status).toBe(503);
    expect((await res.json()).error.code).toBe("SERVICE_UNAVAILABLE");
  });

  it("returns 404 for unsafe paths", async () => {
    const fetchImpl = vi.fn<typeof fetch>();
    const res = await forwardToBackend({
      request: new Request("http://web/api/backend/x"),
      path: [".."],
      token: "t",
      baseUrl: BASE,
      fetchImpl,
    });
    expect(res.status).toBe(404);
    expect(fetchImpl).not.toHaveBeenCalled();
  });
});

describe("session turns", () => {
  it("gives AI turns a longer timeout than plain calls", () => {
    expect(timeoutFor(["sessions", "s1", "messages"])).toBeGreaterThan(timeoutFor(["sessions"]));
    expect(timeoutFor(["sessions", "s1", "end"])).toBe(timeoutFor(["sessions", "s1", "messages"]));
    expect(timeoutFor(["sessions", "s1"])).toBe(timeoutFor(["profile"]));
  });

  it("pipes server-sent events through unbuffered", async () => {
    const stream = new ReadableStream<Uint8Array>({
      start(controller) {
        controller.enqueue(new TextEncoder().encode('event: explanation_chunk\ndata: {"content":"Hi"}\n\n'));
        controller.enqueue(new TextEncoder().encode("event: turn_complete\ndata: {}\n\n"));
        controller.close();
      },
    });
    const fetchImpl = vi
      .fn<typeof fetch>()
      .mockResolvedValue(new Response(stream, { status: 200, headers: { "Content-Type": "text/event-stream" } }));
    const res = await forwardToBackend({
      request: new Request("http://web/api/backend/sessions/s1/messages", {
        method: "POST",
        headers: { Accept: "text/event-stream", "Content-Type": "application/json" },
        body: JSON.stringify({ type: "begin" }),
      }),
      path: ["sessions", "s1", "messages"],
      token: "t",
      baseUrl: BASE,
      fetchImpl,
    });
    expect(new Headers(fetchImpl.mock.calls[0][1]?.headers).get("accept")).toBe("text/event-stream");
    expect(res.headers.get("content-type")).toBe("text/event-stream");
    expect(res.headers.get("x-accel-buffering")).toBe("no");
    expect(await res.text()).toContain("event: turn_complete");
  });
});
