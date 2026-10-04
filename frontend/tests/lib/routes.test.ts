import { describe, expect, it } from "vitest";

import { decideRoute, isPublicPath, safeNextPath } from "@/lib/routes";

describe("decideRoute", () => {
  it("sends anonymous users on protected pages to login, preserving the destination", () => {
    expect(decideRoute("/profile", "?tab=a", false)).toEqual({
      type: "redirect",
      to: "/login?next=%2Fprofile%3Ftab%3Da",
    });
  });

  it("does not add ?next for the root path", () => {
    expect(decideRoute("/", "", false)).toEqual({ type: "redirect", to: "/login" });
  });

  it("allows anonymous users on public pages", () => {
    expect(decideRoute("/login", "", false)).toEqual({ type: "allow" });
    expect(decideRoute("/auth/confirm", "?code=x", false)).toEqual({ type: "allow" });
  });

  it("bounces signed-in users away from the login page", () => {
    expect(decideRoute("/login", "", true)).toEqual({ type: "redirect", to: "/dashboard" });
  });

  it("allows signed-in users everywhere else", () => {
    expect(decideRoute("/dashboard", "", true)).toEqual({ type: "allow" });
  });
});

describe("isPublicPath", () => {
  it.each([
    ["/login", true],
    ["/login/", true],
    ["/auth/confirm", true],
    ["/loginx", false],
    ["/authority", false],
    ["/dashboard", false],
  ])("%s → %s", (path, expected) => {
    expect(isPublicPath(path)).toBe(expected);
  });
});

describe("safeNextPath", () => {
  it.each([
    [null, "/dashboard"],
    ["", "/dashboard"],
    ["/profile", "/profile"],
    ["/profile?x=1", "/profile?x=1"],
    ["https://evil.example", "/dashboard"],
    ["//evil.example", "/dashboard"],
    ["/\\evil.example", "/dashboard"],
    ["javascript:alert(1)", "/dashboard"],
  ])("%s → %s", (input, expected) => {
    expect(safeNextPath(input)).toBe(expected);
  });
});
