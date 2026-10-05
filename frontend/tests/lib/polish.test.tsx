import { act, render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { RouteError } from "@/components/layout/route-states";
import { ThemeToggle } from "@/components/layout/theme-toggle";
import { Toaster } from "@/components/ui/toaster";
import { ApiError } from "@/lib/api";
import { errorMessage, shouldRetry } from "@/lib/errors";
import { formatMinutes, masteryBg, masteryFill } from "@/lib/mastery";
import { applyTheme, readTheme, THEME_KEY, THEME_SCRIPT } from "@/lib/theme";
import { toast, useToastStore } from "@/stores/toast-store";

vi.mock("next/link", () => ({
  default: (props: React.AnchorHTMLAttributes<HTMLAnchorElement>) => <a {...props} />,
}));

function mockMedia(dark: boolean) {
  vi.stubGlobal(
    "matchMedia",
    vi.fn().mockReturnValue({ matches: dark, addEventListener: vi.fn(), removeEventListener: vi.fn() }),
  );
}

describe("theme", () => {
  beforeEach(() => {
    localStorage.clear();
    document.documentElement.classList.remove("dark");
  });
  afterEach(() => vi.unstubAllGlobals());

  it("applies and remembers a choice; system follows the OS", () => {
    mockMedia(true);
    expect(readTheme()).toBe("system");
    applyTheme("light");
    expect(document.documentElement).not.toHaveClass("dark");
    expect(localStorage.getItem(THEME_KEY)).toBe("light");
    applyTheme("dark");
    expect(document.documentElement).toHaveClass("dark");
    expect(readTheme()).toBe("dark");
    applyTheme("system");
    expect(localStorage.getItem(THEME_KEY)).toBeNull();
    expect(document.documentElement).toHaveClass("dark"); // OS prefers dark
  });

  it("pre-paint script applies the stored theme", () => {
    mockMedia(false);
    localStorage.setItem(THEME_KEY, "dark");
    new Function(THEME_SCRIPT)();
    expect(document.documentElement).toHaveClass("dark");
  });

  it("toggle cycles system → light → dark → system", async () => {
    mockMedia(false);
    render(<ThemeToggle />);
    const button = screen.getByTestId("theme-toggle");
    expect(button).toHaveAccessibleName(/System theme/);
    await userEvent.click(button);
    expect(button).toHaveAccessibleName(/Light theme/);
    await userEvent.click(button);
    expect(button).toHaveAccessibleName(/Dark theme/);
    expect(document.documentElement).toHaveClass("dark");
    await userEvent.click(button);
    expect(button).toHaveAccessibleName(/System theme/);
    expect(document.documentElement).not.toHaveClass("dark");
  });
});

describe("toasts", () => {
  beforeEach(() => useToastStore.setState({ toasts: [] }));

  it("shows, caps, dismisses and expires toasts", async () => {
    vi.useFakeTimers();
    render(<Toaster />);
    act(() => {
      toast.success("Saved");
      toast.error("Failed");
      toast.info("FYI");
    });
    expect(screen.getAllByTestId("toast").map((t) => t.dataset.tone)).toEqual(["success", "error", "info"]);
    expect(screen.getByRole("alert")).toHaveTextContent("Failed");
    act(() => {
      for (let i = 0; i < 4; i++) toast.info(`more ${i}`);
    });
    expect(screen.getAllByTestId("toast")).toHaveLength(4);
    act(() => vi.advanceTimersByTime(4100));
    expect(screen.queryAllByTestId("toast")).toHaveLength(0);
    vi.useRealTimers();
    act(() => void toast.error("Stays a while"));
    await userEvent.click(screen.getByRole("button", { name: "Dismiss notification" }));
    expect(screen.queryByTestId("toast")).toBeNull();
  });
});

describe("error policy", () => {
  it("words errors for people", () => {
    expect(errorMessage(new ApiError(0, "NETWORK_ERROR", "x"))).toMatch(/offline/);
    expect(errorMessage(new ApiError(500, "INTERNAL_ERROR", "boom", {}, "rid-1"))).toBe(
      "Something went wrong on our side. (ref rid-1)",
    );
    expect(errorMessage(new ApiError(503, "SERVICE_UNAVAILABLE", "x"))).toBe("Something went wrong on our side.");
    expect(errorMessage(new ApiError(409, "CONFLICT", "Already exists"))).toBe("Already exists");
    expect(errorMessage(new Error("plain"))).toBe("plain");
    expect(errorMessage("weird")).toBe("Something went wrong.");
  });

  it("retries transient failures only", () => {
    expect(shouldRetry(0, new ApiError(503, "SERVICE_UNAVAILABLE", "x"))).toBe(true);
    expect(shouldRetry(0, new ApiError(429, "RATE_LIMITED", "x"))).toBe(true);
    expect(shouldRetry(0, new ApiError(0, "NETWORK_ERROR", "x"))).toBe(true);
    expect(shouldRetry(2, new ApiError(503, "SERVICE_UNAVAILABLE", "x"))).toBe(false);
    expect(shouldRetry(0, new ApiError(404, "RESOURCE_NOT_FOUND", "x"))).toBe(false);
  });
});

describe("helpers and boundaries", () => {
  it("formats study time and mastery colours", () => {
    expect(formatMinutes(45)).toBe("45 min");
    expect(formatMinutes(120)).toBe("2 h");
    expect(formatMinutes(135)).toBe("2 h 15 min");
    expect(masteryBg(0.9)).toBe("bg-emerald-600");
    expect(masteryFill(0.1)).toBe("fill-slate-300");
  });

  it("route error offers a retry and the reference", async () => {
    const reset = vi.fn();
    vi.spyOn(console, "error").mockImplementation(() => {});
    render(<RouteError error={Object.assign(new Error("x"), { digest: "abc123" })} reset={reset} />);
    expect(screen.getByRole("alert")).toHaveTextContent("Reference: abc123");
    await userEvent.click(screen.getByRole("button", { name: "Try again" }));
    expect(reset).toHaveBeenCalled();
    expect(screen.getByRole("link", { name: "Dashboard" })).toHaveAttribute("href", "/dashboard");
  });
});
