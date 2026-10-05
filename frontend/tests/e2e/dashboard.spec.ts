/**
 * Phase 7 journey: the onboarding guide takes a new user from sign-up to their first session
 * (AC-7.4); the dashboard and progress page then show accurate mastery (AC-7.1); the concept
 * map is interactive and colour-coded (AC-7.2); dark mode persists across reloads; toasts
 * confirm actions.
 */
import { expect, test } from "@playwright/test";

import { answer, endSession, signUp, uploadCalculus } from "./support/journey";

test("onboarding → first session → dashboard, progress, concept map, dark mode", async ({ page }) => {
  test.setTimeout(240_000);
  await signUp(page, "Dash Tester");
  const nav = page.getByRole("navigation", { name: "Main" });

  // AC-7.4: the guide points at the next step each time.
  const guide = page.getByTestId("onboarding");
  await expect(guide).toContainText("0 of 4 steps done");
  await guide.getByRole("link", { name: "Set up profile" }).click();
  await page.getByLabel("Grade level").fill("11th grade");
  await page.getByRole("button", { name: "Save changes" }).click();
  await expect(page.getByText("Profile saved.")).toBeVisible();
  await nav.getByRole("link", { name: "Dashboard" }).click();
  await expect(page.getByTestId("onboarding-step-profile")).toHaveAttribute("data-done", "true");
  await expect(guide.getByRole("link", { name: "Upload" })).toBeVisible();

  await uploadCalculus(page);
  await expect(page.getByTestId("toast").filter({ hasText: "is ready to study" })).toBeVisible();
  await nav.getByRole("link", { name: "Dashboard" }).click();
  await expect(guide).toContainText("3 of 4 steps done");
  await guide.getByRole("link", { name: "Start studying" }).click();
  await expect(page).toHaveURL(/\/session$/);
  await page.getByRole("button", { name: "Start session" }).click();
  await expect(page).toHaveURL(/\/session\/[0-9a-f-]{36}$/);
  const right = await answer(page, 0);
  await expect(right).toHaveAttribute("data-correct", "true");
  await endSession(page);

  await nav.getByRole("link", { name: "Dashboard" }).click();
  await expect(guide).toContainText("You're all set");
  await guide.getByRole("button", { name: "Done" }).click();
  await expect(guide).toBeHidden();

  // AC-7.1: accurate mastery data.
  const overview = page.getByTestId("mastery-overview");
  await expect(overview).toContainText("/ 3"); // three concepts extracted from the PDF
  await expect(overview).toContainText("1 day"); // studied today → streak
  await expect(overview).toContainText("1 session");
  await expect(page.getByTestId("subject-mastery")).toContainText("Unsorted");
  await expect(page.getByTestId("activity-bar").last()).toHaveAttribute("data-active", "true");

  // Progress page: heatmap, misconceptions, AI usage.
  await nav.getByRole("link", { name: "Progress" }).click();
  await expect(page.getByTestId("heatmap-row")).toHaveCount(3);
  await expect(page.getByTestId("heatmap-row").first().getByTestId("heatmap-cell").last()).not.toHaveClass(/bg-slate-100/);
  await expect(page.getByTestId("misconception-list")).toBeVisible();
  await expect(page.getByTestId("ai-usage")).not.toContainText("No AI calls in this period");

  // AC-7.2: interactive, colour-coded concept map.
  await nav.getByRole("link", { name: "Concepts" }).click();
  await page.getByTestId("concept-row").filter({ hasText: "Derivatives" }).click();
  const map = page.getByTestId("concept-graph");
  await expect(map.getByTestId("graph-node")).toHaveCount(3);
  await expect(map.getByTestId("graph-edge")).toHaveCount(2);
  await expect(map.locator('[data-target="true"] rect')).toHaveClass(/fill-/);
  await map.getByRole("link", { name: /Chain Rule/ }).click();
  await expect(page.getByRole("heading", { name: "Chain Rule" })).toBeVisible();

  // Dark mode: toggled, persisted, applied before paint on reload.
  const html = page.locator("html");
  await expect(html).not.toHaveClass(/dark/);
  await page.getByTestId("theme-toggle").click(); // system → light
  await page.getByTestId("theme-toggle").click(); // light → dark
  await expect(html).toHaveClass(/dark/);
  await page.reload({ waitUntil: "domcontentloaded" });
  await expect(html).toHaveClass(/dark/);
  const background = await page.evaluate(() => getComputedStyle(document.body).backgroundColor);
  expect(background).not.toBe("rgb(248, 250, 252)"); // not the light slate-50

  // Toasts confirm actions.
  await nav.getByRole("link", { name: "Dashboard" }).click();
  await page.getByTestId("subjects-card").getByRole("button", { name: "Add subject" }).click();
  await page.getByLabel("Subject name").fill("Calculus");
  await page.getByTestId("subjects-card").getByRole("button", { name: "Save" }).click();
  await expect(page.getByTestId("toast").filter({ hasText: "Subject added" })).toBeVisible();
});
