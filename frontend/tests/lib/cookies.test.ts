import { describe, expect, it } from "vitest";

import { hardenAuthCookie } from "@/lib/cookies";

describe("hardenAuthCookie", () => {
  it("forces httpOnly + sameSite=strict on session cookies, overriding library defaults", () => {
    const opts = hardenAuthCookie(
      "sb-abc-auth-token",
      { httpOnly: false, sameSite: "lax", maxAge: 100 },
      true,
    );
    expect(opts).toMatchObject({
      httpOnly: true,
      sameSite: "strict",
      secure: true,
      path: "/",
      maxAge: 100,
    });
  });

  it("is not secure in development (http://localhost)", () => {
    expect(hardenAuthCookie("sb-abc-auth-token.0", {}, false).secure).toBe(false);
  });

  it("uses lax for the PKCE code verifier so e-mail confirmation links work", () => {
    expect(hardenAuthCookie("sb-abc-auth-token-code-verifier", {}, true)).toMatchObject({
      httpOnly: true,
      sameSite: "lax",
    });
  });

  it("keeps an explicit path", () => {
    expect(hardenAuthCookie("x", { path: "/auth" }, true).path).toBe("/auth");
  });
});
