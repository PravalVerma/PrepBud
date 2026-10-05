import { describe, expect, it } from "vitest";

import { addDays, cn, dayLabel, formatPercent, initials, isoDay, masteryLabel, parseDay } from "@/lib/utils";

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

describe("calendar days", () => {
  it("formats and shifts date-only values without timezone drift", () => {
    expect(isoDay(new Date(2026, 0, 31))).toBe("2026-01-31");
    expect(parseDay("2026-03-01").getDate()).toBe(1);
    expect(addDays("2026-02-28", 1)).toBe("2026-03-01");
    expect(addDays("2026-01-01", -1)).toBe("2025-12-31");
  });

  it("labels days relative to today", () => {
    const today = "2026-10-05";
    expect(dayLabel(today, today)).toBe("Today");
    expect(dayLabel("2026-10-06", today)).toBe("Tomorrow");
    expect(dayLabel("2026-10-04", today)).toBe("Yesterday");
    expect(dayLabel("2026-10-09", today)).toMatch(/9/);
  });
});
