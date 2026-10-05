import { render, screen } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { Sidebar } from "@/components/layout/sidebar";
import { useUIStore } from "@/stores/ui-store";

const pathname = vi.hoisted(() => ({ current: "/dashboard" }));
vi.mock("next/navigation", () => ({ usePathname: () => pathname.current }));
vi.mock("next/link", () => ({
  default: ({ onClick, ...props }: React.AnchorHTMLAttributes<HTMLAnchorElement>) => (
    <a
      {...props}
      onClick={(e) => {
        e.preventDefault(); // jsdom cannot navigate
        onClick?.(e);
      }}
    />
  ),
}));

describe("Sidebar", () => {
  beforeEach(() => {
    useUIStore.setState({ mobileNavOpen: false });
  });

  it("links to the live pages and marks the current one", () => {
    render(<Sidebar />);

    const dashboard = screen.getByRole("link", { name: /dashboard/i });
    const profile = screen.getByRole("link", { name: /profile/i });
    expect(dashboard).toHaveAttribute("href", "/dashboard");
    expect(dashboard).toHaveAttribute("aria-current", "page");
    expect(profile).not.toHaveAttribute("aria-current");
  });

  it("shows later-phase pages as disabled, not links", () => {
    render(<Sidebar />);

    expect(screen.queryByRole("link", { name: /goals/i })).toBeNull();
    expect(screen.getByText("Goals").closest("[aria-disabled]")).toHaveAttribute(
      "aria-disabled",
      "true",
    );
  });

  it("links to the Phase 3 content pages", () => {
    render(<Sidebar />);
    expect(screen.getByRole("link", { name: /upload material/i })).toHaveAttribute("href", "/upload");
    expect(screen.getByRole("link", { name: /concepts/i })).toHaveAttribute("href", "/concepts");
  });

  it("marks nested routes active", () => {
    pathname.current = "/profile/settings";
    render(<Sidebar />);
    expect(screen.getByRole("link", { name: /profile/i })).toHaveAttribute("aria-current", "page");
    pathname.current = "/dashboard";
  });

  it("closes the mobile drawer when a link is chosen", () => {
    useUIStore.setState({ mobileNavOpen: true });
    render(<Sidebar />);
    screen.getByRole("link", { name: /profile/i }).click();
    expect(useUIStore.getState().mobileNavOpen).toBe(false);
  });
});
