/**
 * WebSocket channel for a learning session (API_CONTRACT §3.9).
 *
 * The browser never holds the user's JWT, so each connection first asks the BFF for a
 * single-use ticket (`POST /sessions/{id}/ws-ticket`) and opens the socket with it. A
 * dropped connection is retried with exponential backoff (the server paused the session
 * meanwhile and re-sends the current question/explanation on reconnect). A periodic
 * `ping` keeps idle connections from being reaped by proxies.
 */
import { ApiError } from "@/lib/api";
import type { ClientMessage, ServerEvent, WsTicket } from "@/types/session";

export type SocketStatus = "connecting" | "open" | "reconnecting" | "closed" | "failed";

export interface SessionSocketOptions {
  sessionId: string;
  /** ws(s):// origin of the API, e.g. `wss://api.example.com`. */
  baseUrl: string;
  getTicket: (sessionId: string) => Promise<WsTicket>;
  onEvent: (event: ServerEvent) => void;
  onStatus: (status: SocketStatus) => void;
  WebSocketImpl?: typeof WebSocket;
  maxRetries?: number;
  pingIntervalMs?: number;
  backoffMs?: (attempt: number) => number;
}

const NORMAL_CLOSURE = 1000;

export const defaultBackoff = (attempt: number) => Math.min(500 * 2 ** attempt, 8_000);

export function socketUrl(baseUrl: string, ticket: WsTicket): string {
  return `${baseUrl.replace(/\/+$/, "")}${ticket.websocket_path}?ticket=${encodeURIComponent(ticket.ticket)}`;
}

export class SessionSocket {
  private socket: WebSocket | null = null;
  private attempt = 0;
  private stopped = false;
  private retryTimer: ReturnType<typeof setTimeout> | null = null;
  private pingTimer: ReturnType<typeof setInterval> | null = null;
  private readonly opts: Required<SessionSocketOptions>;

  constructor(options: SessionSocketOptions) {
    this.opts = {
      WebSocketImpl: globalThis.WebSocket,
      maxRetries: 6,
      pingIntervalMs: 25_000,
      backoffMs: defaultBackoff,
      ...options,
    };
  }

  get isOpen(): boolean {
    return this.socket?.readyState === 1;
  }

  async connect(): Promise<void> {
    if (this.stopped) return;
    this.opts.onStatus(this.attempt === 0 ? "connecting" : "reconnecting");
    let ticket: WsTicket;
    try {
      ticket = await this.opts.getTicket(this.opts.sessionId);
    } catch (error) {
      // 404: not this user's session; 409: it has ended — reconnecting will not help.
      if (error instanceof ApiError && (error.status === 404 || error.status === 409)) {
        this.stop(error.status === 409 ? "closed" : "failed");
      } else {
        this.scheduleRetry();
      }
      return;
    }
    if (this.stopped) return;

    const socket = new this.opts.WebSocketImpl(socketUrl(this.opts.baseUrl, ticket));
    this.socket = socket;
    socket.onopen = () => {
      this.attempt = 0;
      this.opts.onStatus("open");
      this.pingTimer = setInterval(() => this.send({ type: "ping" }), this.opts.pingIntervalMs);
    };
    socket.onmessage = (message: MessageEvent) => {
      let event: ServerEvent;
      try {
        event = JSON.parse(String(message.data)) as ServerEvent;
      } catch {
        return;
      }
      if (event && typeof event.type === "string" && event.type !== "pong") this.opts.onEvent(event);
    };
    socket.onclose = (event: CloseEvent) => {
      this.clearPing();
      if (this.socket !== socket) return;
      this.socket = null;
      if (this.stopped) return;
      if (event.code === NORMAL_CLOSURE) this.stop("closed");
      else this.scheduleRetry();
    };
  }

  /** False when the socket is not open (the caller decides how to recover). */
  send(message: ClientMessage | { type: "ping" }): boolean {
    if (!this.socket || this.socket.readyState !== 1) return false;
    this.socket.send(JSON.stringify("payload" in message ? message : { ...message, payload: {} }));
    return true;
  }

  /** Close for good (page left or session over); no reconnect. */
  close(): void {
    this.stop("closed");
  }

  /** Manual retry after `failed`. */
  retry(): void {
    this.stopped = false;
    this.attempt = 0;
    void this.connect();
  }

  private stop(status: SocketStatus): void {
    this.stopped = true;
    if (this.retryTimer) clearTimeout(this.retryTimer);
    this.clearPing();
    const socket = this.socket;
    this.socket = null;
    if (socket && socket.readyState <= 1) socket.close(NORMAL_CLOSURE);
    this.opts.onStatus(status);
  }

  private scheduleRetry(): void {
    if (this.stopped) return;
    if (this.attempt >= this.opts.maxRetries) {
      this.stopped = true;
      this.opts.onStatus("failed");
      return;
    }
    this.opts.onStatus("reconnecting");
    const delay = this.opts.backoffMs(this.attempt);
    this.attempt += 1;
    this.retryTimer = setTimeout(() => void this.connect(), delay);
  }

  private clearPing(): void {
    if (this.pingTimer) clearInterval(this.pingTimer);
    this.pingTimer = null;
  }
}
