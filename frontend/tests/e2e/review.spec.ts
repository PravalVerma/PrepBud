/**
 * Phase 6 journey (TEST_STRATEGY §3.4 "Review flow"): set a deadline goal → the study plan
 * is generated (AC-6.3) in prerequisite order → study today's items → the item is ticked off
 * and its next review is scheduled (AC-6.1) → skip / undo / move items → start a review
 * session (AC-6.6) → dashboard and goal progress reflect it.
 *
 * Time-dependent behaviour (overdue ordering, interval growth, decay) is covered by the
 * backend integration tests, which control the clock.
 */
import { expect, test } from "@playwright/test";

import { answer, endSession, signUp, uploadCalculus } from "./support/journey";

function isoDay(offset = 0): string {
  const d = new Date();
  d.setDate(d.getDate() + offset);
  return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, "0")}-${String(d.getDate()).padStart(2, "0")}`;
}

test("goal → study plan → study → review", async ({ page }) => {
  test.setTimeout(240_000);
  await signUp(page, "Review Tester");
  await uploadCalculus(page);
  const nav = page.getByRole("navigation", { name: "Main" });

  // AC-6.3: a deadline goal over three concepts generates the plan.
  await nav.getByRole("link", { name: "Goals" }).click();
  await expect(page.getByText(/No active goals/)).toBeVisible();
  await page.getByRole("button", { name: "New goal" }).click();
  const form = page.getByTestId("goal-form");
  await form.getByLabel("Goal", { exact: true }).fill("Master calculus");
  for (const name of ["Chain Rule", "Derivatives", "Limits"]) {
    await form.getByRole("checkbox", { name: new RegExp(name) }).check();
  }
  await form.getByLabel("Kind of goal").selectOption("deadline");
  await form.getByLabel("Target date").fill(isoDay(1));
  await form.getByRole("button", { name: "Create goal & plan" }).click();
  await expect(page.getByText(/your plan has 3 items, 2 for today/)).toBeVisible();
  const card = page.getByTestId("goal-card");
  await expect(card).toContainText("0 of 3 concepts mastered");
  await expect(card).toContainText("1 day left");

  // The plan: prerequisites first, spread to the deadline.
  await nav.getByRole("link", { name: "Review" }).click();
  await expect(page.getByTestId("due-count")).toHaveText("2 items due today");
  const today = page.getByTestId(`plan-group-${isoDay()}`);
  await expect(today.getByTestId("plan-item")).toHaveCount(2);
  await expect(today.getByTestId("plan-item").first()).toContainText("Limits");
  await expect(today.getByTestId("plan-item").nth(1)).toContainText("Derivatives");
  await expect(page.getByTestId(`plan-group-${isoDay(1)}`)).toContainText("Chain Rule");

  // Study today's plan: Limits comes first (it is Derivatives' prerequisite).
  await page.getByRole("button", { name: "Study today's plan" }).click();
  await expect(page).toHaveURL(/\/session\/[0-9a-f-]{36}$/);
  await expect(page.getByTestId("connection-status")).toHaveText("Live", { timeout: 15_000 });
  const right = await answer(page, 0);
  await expect(right).toHaveAttribute("data-correct", "true");
  await endSession(page);

  // AC-6.1: Limits is done for today and has its next review date.
  await nav.getByRole("link", { name: "Review" }).click();
  const done = page.getByTestId("plan-group-done");
  const limits = done.getByTestId("plan-item").filter({ hasText: "Limits" });
  await expect(limits).toHaveAttribute("data-status", "completed");
  await expect(limits).toContainText("next review");
  await expect(page.getByTestId("due-count")).toHaveText("1 item due today");

  // Skip, undo, move to tomorrow.
  const derivatives = () => page.getByTestId("plan-item").filter({ hasText: "Derivatives" });
  await derivatives().getByRole("button", { name: "Skip" }).click();
  await expect(derivatives()).toHaveAttribute("data-status", "skipped");
  await derivatives().getByRole("button", { name: "Undo" }).click();
  await expect(derivatives()).toHaveAttribute("data-status", "pending");
  await derivatives().getByRole("button", { name: "Later" }).click();
  await expect(page.getByTestId(`plan-group-${isoDay(1)}`)).toContainText("Derivatives");
  await expect(page.getByTestId("due-count")).toHaveText("You're all caught up");

  // AC-6.6: a review session (nothing is due yet, so it refreshes what was practised).
  await page.getByRole("button", { name: "Review anyway" }).click();
  await expect(page).toHaveURL(/\/session\/[0-9a-f-]{36}$/);
  await expect(page.getByTestId("session-room")).toContainText("Limits", { timeout: 15_000 });
  await endSession(page);

  // Dashboard and goal progress.
  await nav.getByRole("link", { name: "Dashboard" }).click();
  await expect(page.getByTestId("review-queue")).toContainText("You're all caught up");
  await nav.getByRole("link", { name: "Goals" }).click();
  await expect(page.getByTestId("goal-card").getByRole("progressbar")).not.toHaveAttribute("aria-valuenow", "0");
});
