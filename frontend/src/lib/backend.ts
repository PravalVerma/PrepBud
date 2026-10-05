/**
 * Server-side forwarding of browser API calls to FastAPI (backend-for-frontend).
 *
 * The browser calls same-origin `/api/backend/<path>`; this attaches the user's
 * access token (held in an httpOnly cookie) as `Authorization: Bearer` and relays
 * the response verbatim, preserving the API_CONTRACT envelope, status codes,
 * request IDs and rate-limit headers. Server-sent event streams (session turns with
 * `Accept: text/event-stream`) are piped through unbuffered.
 */
import "server-only";

const FORWARDED_REQUEST_HEADERS = ["content-type", "accept", "x-request-id"];
const FORWARDED_RESPONSE_HEADERS = [
  "content-type",
  "x-request-id",
  "x-ratelimit-limit",
  "x-ratelimit-remaining",
  "x-ratelimit-reset",
  "retry-after",
  "x-search-mode",
  "x-search-degraded",
];
const SAFE_SEGMENT = /^[A-Za-z0-9._~-]+$/;
const TIMEOUT_MS = 15_000;
/** Session turns run the tutor/evaluator models; a slow (local) model can take a while. */
const AI_TURN_TIMEOUT_MS = 180_000;

export function timeoutFor(path: string[]): number {
  return path[0] === "sessions" && path.length >= 3 ? AI_TURN_TIMEOUT_MS : TIMEOUT_MS;
}

export type ForwardOptions = {
  request: Request;
  path: string[];
  token: string | null;
  baseUrl: string;
  fetchImpl?: typeof fetch;
};

function errorResponse(status: number, code: string, message: string): Response {
  return Response.json(
    {
      error: { code, message, details: {} },
      meta: { request_id: null, timestamp: new Date().toISOString() },
    },
    { status, headers: { "Cache-Control": "no-store" } },
  );
}

export function buildBackendUrl(baseUrl: string, path: string[], search: string): string | null {
  if (path.length === 0 || path.some((s) => !SAFE_SEGMENT.test(s) || s === "." || s === "..")) {
    return null;
  }
  return `${baseUrl.replace(/\/+$/, "")}/${path.join("/")}${search}`;
}

export async function forwardToBackend({
  request,
  path,
  token,
  baseUrl,
  fetchImpl = fetch,
}: ForwardOptions): Promise<Response> {
  if (!token) {
    return errorResponse(401, "AUTHENTICATION_REQUIRED", "Authentication required");
  }
  const target = buildBackendUrl(baseUrl, path, new URL(request.url).search);
  if (!target) {
    return errorResponse(404, "RESOURCE_NOT_FOUND", "Resource not found");
  }

  const headers = new Headers({ Authorization: `Bearer ${token}` });
  for (const name of FORWARDED_REQUEST_HEADERS) {
    const value = request.headers.get(name);
    if (value) headers.set(name, value);
  }
  const hasBody = !["GET", "HEAD"].includes(request.method);

  let upstream: Response;
  try {
    upstream = await fetchImpl(target, {
      method: request.method,
      headers,
      body: hasBody ? await request.arrayBuffer() : undefined,
      cache: "no-store",
      redirect: "manual",
      signal: AbortSignal.timeout(timeoutFor(path)),
    });
  } catch {
    return errorResponse(503, "SERVICE_UNAVAILABLE", "The learning service is unavailable");
  }

  const responseHeaders = new Headers({ "Cache-Control": "no-store" });
  for (const name of FORWARDED_RESPONSE_HEADERS) {
    const value = upstream.headers.get(name);
    if (value) responseHeaders.set(name, value);
  }
  if (upstream.headers.get("content-type")?.startsWith("text/event-stream")) {
    responseHeaders.set("X-Accel-Buffering", "no"); // no proxy buffering of the stream
    return new Response(upstream.body, { status: upstream.status, headers: responseHeaders });
  }
  const body = upstream.status === 204 ? null : await upstream.arrayBuffer();
  return new Response(body, { status: upstream.status, headers: responseHeaders });
}
