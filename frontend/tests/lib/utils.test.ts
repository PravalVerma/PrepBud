import { describe, expect, it } from "vitest";

import { cn, formatPercent, initials, masteryLabel } from "@/lib/utils";

describe("masteryLabel", () => {
  it.each([
    [0, "novice"],
    [0.19, "novice"],
    [0.2, "beginner"],
    [0.4, "intermediate"],
    [0.6, "proficient"],
    [0.79, "proficient"],
    [0.8, "mastered"],
    [1, "mastered"],
  ])("%d → %s", (level, label) => {
    expect(masteryLabel(level)).toBe(label);
  });
});

describe("helpers", () => {
  it("cn drops falsy classes", () => {
    expect(cn("a", false, null, undefined, "b")).toBe("a b");
  });

  it("formatPercent rounds", () => {
    expect(formatPercent(0.625)).toBe("63%");
  });

  it.each([
    ["Jane Doe", "JD"],
    ["jane.doe@example.com", "JD"],
    ["solo", "S"],
    [null, "?"],
    ["", "?"],
  ])("initials(%s) = %s", (input, expected) => {
    expect(initials(input)).toBe(expected);
  });
});
