import { afterEach, describe, expect, it, vi } from "vitest";

import { env, toWsOrigin } from "@/lib/env";

afterEach(() => vi.unstubAllEnvs());

describe("WebSocket origin", () => {
  it.each([
    ["http://localhost:8001/api/v1", "ws://localhost:8001"],
    ["https://api.example.com/api/v1/", "wss://api.example.com"],
    ["wss://api.example.com", "wss://api.example.com"],
  ])("%s → %s", (url, origin) => {
    expect(toWsOrigin(url)).toBe(origin);
  });

  it("prefers PUBLIC_API_URL over API_URL", () => {
    vi.stubEnv("API_URL", "http://api:8000/api/v1");
    vi.stubEnv("PUBLIC_API_URL", "");
    expect(env.wsUrl).toBe("ws://api:8000");
    vi.stubEnv("PUBLIC_API_URL", "https://api.example.com");
    expect(env.wsUrl).toBe("wss://api.example.com");
  });
});
