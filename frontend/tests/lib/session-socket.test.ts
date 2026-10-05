import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { ApiError } from "@/lib/api";
import { defaultBackoff, SessionSocket, type SocketStatus, socketUrl } from "@/lib/session-socket";
import type { ServerEvent, WsTicket } from "@/types/session";

class FakeSocket {
  static instances: FakeSocket[] = [];
  readyState = 0;
  sent: string[] = [];
  closedWith: number | null = null;
  onopen: (() => void) | null = null;
  onmessage: ((e: { data: string }) => void) | null = null;
  onclose: ((e: { code: number }) => void) | null = null;

  constructor(readonly url: string) {
    FakeSocket.instances.push(this);
  }
  open() {
    this.readyState = 1;
    this.onopen?.();
  }
  message(data: unknown) {
    this.onmessage?.({ data: typeof data === "string" ? data : JSON.stringify(data) });
  }
  drop(code = 1006) {
    this.readyState = 3;
    this.onclose?.({ code });
  }
  send(data: string) {
    this.sent.push(data);
  }
  close(code: number) {
    this.closedWith = code;
    this.drop(code);
  }
}

const ticket = (n = 1): WsTicket => ({
  ticket: `tk/${n}`,
  expires_in_seconds: 60,
  websocket_path: "/api/v1/sessions/s1/ws",
});

function setup(getTicket = vi.fn(async () => ticket())) {
  const events: ServerEvent[] = [];
  const statuses: SocketStatus[] = [];
  const socket = new SessionSocket({
    sessionId: "s1",
    baseUrl: "ws://api.local/",
    getTicket,
    onEvent: (e) => events.push(e),
    onStatus: (s) => statuses.push(s),
    WebSocketImpl: FakeSocket as unknown as typeof WebSocket,
    maxRetries: 2,
    pingIntervalMs: 1_000,
    backoffMs: () => 100,
  });
  return { socket, events, statuses, getTicket };
}

const last = () => FakeSocket.instances.at(-1)!;

beforeEach(() => {
  FakeSocket.instances = [];
  vi.useFakeTimers();
});
afterEach(() => vi.useRealTimers());

describe("session socket", () => {
  it("builds the URL from the ticket", () => {
    expect(socketUrl("wss://api.example.com/", ticket())).toBe(
      "wss://api.example.com/api/v1/sessions/s1/ws?ticket=tk%2F1",
    );
    expect([0, 1, 5, 10].map(defaultBackoff)).toEqual([500, 1000, 8000, 8000]);
  });

  it("connects with a ticket, relays events, pings and sends", async () => {
    const { socket, events, statuses } = setup();
    await socket.connect();
    expect(last().url).toBe("ws://api.local/api/v1/sessions/s1/ws?ticket=tk%2F1");
    expect(socket.send({ type: "request_hint", payload: {} })).toBe(false); // not open yet

    last().open();
    expect(statuses).toEqual(["connecting", "open"]);
    last().message({ type: "hint", payload: { hint_number: 1, content: "x" } });
    last().message({ type: "pong", payload: {} });
    last().message("not json");
    expect(events.map((e) => e.type)).toEqual(["hint"]);

    expect(socket.send({ type: "student_question", payload: { content: "Why?" } })).toBe(true);
    vi.advanceTimersByTime(1_000);
    expect(last().sent.map((s) => JSON.parse(s).type)).toEqual(["student_question", "ping"]);
    expect(JSON.parse(last().sent[1])).toEqual({ type: "ping", payload: {} });
  });

  it("reconnects with a fresh ticket after a drop, then gives up", async () => {
    let n = 0;
    const { socket, statuses, getTicket } = setup(vi.fn(async () => ticket(++n)));
    await socket.connect();
    last().open();
    last().drop();
    expect(statuses.at(-1)).toBe("reconnecting");
    await vi.advanceTimersByTimeAsync(100);
    expect(getTicket).toHaveBeenCalledTimes(2);
    expect(last().url).toContain("tk%2F2");

    last().open(); // a successful open resets the retry budget
    last().drop();
    await vi.advanceTimersByTimeAsync(100);
    last().drop();
    await vi.advanceTimersByTimeAsync(100);
    last().drop();
    expect(statuses.at(-1)).toBe("failed");

    socket.retry();
    await vi.advanceTimersByTimeAsync(0);
    last().open();
    expect(statuses.at(-1)).toBe("open");
  });

  it("stops on a normal close (session over)", async () => {
    const { socket, statuses } = setup();
    await socket.connect();
    last().open();
    last().drop(1000);
    expect(statuses.at(-1)).toBe("closed");
    await vi.advanceTimersByTimeAsync(1_000);
    expect(FakeSocket.instances).toHaveLength(1);
    expect(socket.isOpen).toBe(false);
  });

  it("does not retry when the session is gone or over", async () => {
    const gone = setup(vi.fn(async () => Promise.reject(new ApiError(404, "RESOURCE_NOT_FOUND", "x"))));
    await gone.socket.connect();
    expect(gone.statuses.at(-1)).toBe("failed");

    const over = setup(vi.fn(async () => Promise.reject(new ApiError(409, "CONFLICT", "ended"))));
    await over.socket.connect();
    expect(over.statuses.at(-1)).toBe("closed");
    expect(FakeSocket.instances).toHaveLength(0);
  });

  it("retries when the ticket request fails transiently", async () => {
    let calls = 0;
    const { socket, statuses } = setup(
      vi.fn(async () => {
        calls += 1;
        if (calls === 1) throw new ApiError(0, "NETWORK_ERROR", "offline");
        return ticket();
      }),
    );
    await socket.connect();
    expect(statuses).toEqual(["connecting", "reconnecting"]);
    await vi.advanceTimersByTimeAsync(100);
    expect(FakeSocket.instances).toHaveLength(1);
  });

  it("close() is final and closes the socket normally", async () => {
    const { socket, statuses } = setup();
    await socket.connect();
    last().open();
    socket.close();
    expect(last().closedWith).toBe(1000);
    expect(statuses.at(-1)).toBe("closed");
    await vi.advanceTimersByTimeAsync(5_000);
    expect(FakeSocket.instances).toHaveLength(1);
  });
});
