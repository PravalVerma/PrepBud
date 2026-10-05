/**
 * AC-7.5 — tablet-sized screens (run by the "tablet" project, iPad viewport): the navigation
 * is a drawer, and no page scrolls sideways.
 */
import { expect, test } from "@playwright/test";

import { signUp, uploadCalculus } from "./support/journey";

const PAGES = ["/dashboard", "/upload", "/concepts", "/session", "/goals", "/review", "/progress", "/profile"];

test("the app is usable on a tablet", async ({ page }) => {
  test.setTimeout(180_000);
  await signUp(page, "Tablet Tester");

  // Navigation is a drawer opened from the header.
  const toggle = page.getByRole("button", { name: "Toggle navigation" });
  await expect(toggle).toBeVisible();
  await toggle.click();
  await page.getByRole("navigation", { name: "Main" }).getByRole("link", { name: "Upload material" }).click();
  await expect(page).toHaveURL(/\/upload$/);
  await expect(toggle).toHaveAttribute("aria-expanded", "false"); // closes after navigating

  await uploadCalculus(page);
  for (const path of PAGES) {
    await page.goto(path);
    await page.waitForLoadState("networkidle");
    const overflow = await page.evaluate(() => document.documentElement.scrollWidth - window.innerWidth);
    expect(overflow, `${path} overflows horizontally by ${overflow}px`).toBeLessThanOrEqual(1);
  }

  // A concept page with its map fits too (the map scrolls inside its own box).
  await page.goto("/concepts");
  await page.getByTestId("concept-row").filter({ hasText: "Derivatives" }).click();
  await expect(page.getByTestId("concept-graph").getByTestId("graph-node").first()).toBeVisible();
  const overflow = await page.evaluate(() => document.documentElement.scrollWidth - window.innerWidth);
  expect(overflow).toBeLessThanOrEqual(1);
});
