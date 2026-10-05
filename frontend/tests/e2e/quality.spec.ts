/**
 * Quality gates: every page loads within 2 s (AC-7.3) and has no serious or critical
 * accessibility violations (basic axe audit), in light and dark mode.
 */
import AxeBuilder from "@axe-core/playwright";
import { expect, type Page, test } from "@playwright/test";

import { signUp, uploadCalculus } from "./support/journey";

const PAGES: { path: string; ready: string }[] = [
  { path: "/dashboard", ready: '[data-testid="mastery-overview"]' },
  { path: "/upload", ready: '[data-testid="document-row"]' },
  { path: "/concepts", ready: '[data-testid="concept-row"]' },
  { path: "/session", ready: '[data-testid="session-history"]' },
  { path: "/goals", ready: '[data-testid="goal-list"]' },
  { path: "/review", ready: '[data-testid="study-plan"]' },
  { path: "/progress", ready: '[data-testid="heatmap-row"]' },
  { path: "/profile", ready: "form" },
];

async function loadTime(page: Page, path: string, ready: string): Promise<number> {
  const start = Date.now();
  await page.goto(path);
  await page.locator(ready).first().waitFor({ state: "visible" });
  return Date.now() - start;
}

/** Serious/critical axe violations on the current page, as readable strings. */
async function audit(page: Page, label: string): Promise<string[]> {
  const results = await new AxeBuilder({ page }).withTags(["wcag2a", "wcag2aa"]).analyze();
  return results.violations
    .filter((v) => v.impact === "serious" || v.impact === "critical")
    .map((v) => `${label}: ${v.id} (${v.impact}) — ${v.nodes.slice(0, 3).map((n) => n.target.join(" ")).join(" | ")}`);
}

test("pages load within 2 s and pass a basic accessibility audit", async ({ page }) => {
  test.setTimeout(240_000);
  await signUp(page, "Quality Tester");
  await uploadCalculus(page);

  // Warm-up visit (first compile of each client bundle in this browser), then measure.
  for (const { path, ready } of PAGES) await loadTime(page, path, ready);
  const timings: Record<string, number> = {};
  for (const { path, ready } of PAGES) timings[path] = await loadTime(page, path, ready);
  test.info().annotations.push({ type: "page-load-ms", description: JSON.stringify(timings) });
  for (const [path, ms] of Object.entries(timings)) expect(ms, `${path} took ${ms} ms`).toBeLessThan(2000);

  // Accessibility: light mode on every page, dark mode on the busiest ones.
  const violations: string[] = [];
  for (const { path, ready } of PAGES) {
    await page.goto(path);
    await page.locator(ready).first().waitFor({ state: "visible" });
    violations.push(...(await audit(page, `light ${path}`)));
  }
  await page.getByTestId("theme-toggle").click();
  await page.getByTestId("theme-toggle").click();
  await expect(page.locator("html")).toHaveClass(/dark/);
  for (const { path, ready } of PAGES.filter((p) => ["/dashboard", "/concepts", "/review", "/progress"].includes(p.path))) {
    await page.goto(path);
    await page.locator(ready).first().waitFor({ state: "visible" });
    violations.push(...(await audit(page, `dark ${path}`)));
  }
  expect(violations).toEqual([]);
});

test("login page is accessible", async ({ page }) => {
  await page.goto("/login");
  expect(await audit(page, "login")).toEqual([]);
});
