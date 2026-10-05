/**
 * Phase 5 journey (TEST_STRATEGY §3.4 "Learning session"): sign up → upload material →
 * start a session on a concept → streamed explanation with LaTeX (AC-5.1, AC-5.6) →
 * answer questions with immediate evaluation and mastery updates (AC-5.2, AC-5.4) →
 * end the session and see the summary (AC-5.5) → find it in the history.
 *
 * The browser talks to FastAPI over a real WebSocket (ticket from the BFF); the tutor,
 * question generator and evaluator are the OpenAI-compatible mock LLM, whose MCQ
 * questions always have option A correct.
 */
import { expect, type Locator, type Page, test } from "@playwright/test";

import { signUp, uploadCalculus } from "./support/journey";

/** Waits for the current turn, acknowledging explanations until a question is open. */
async function openQuestion(page: Page): Promise<Locator> {
  const room = page.getByTestId("session-room");
  const active = page
    .getByTestId("question-card")
    .filter({ has: page.getByRole("button", { name: "Submit answer" }) });
  for (let i = 0; i < 6; i++) {
    await expect(room).toHaveAttribute("data-busy", "false", { timeout: 30_000 });
    await expect(room).toHaveAttribute("data-awaiting", /^(acknowledgement|answer)$/, { timeout: 30_000 });
    if ((await room.getAttribute("data-awaiting")) === "answer") return active;
    await page.getByRole("button", { name: "Got it" }).click();
  }
  throw new Error("no question was asked");
}

async function answer(page: Page, option: number): Promise<Locator> {
  const card = await openQuestion(page);
  await card.getByTestId("answer-option").nth(option).click();
  await card.getByRole("button", { name: "Submit answer" }).click();
  const evaluation = page.getByTestId("evaluation").last();
  await expect(page.getByTestId("session-room")).toHaveAttribute("data-busy", "false", { timeout: 30_000 });
  return evaluation;
}

test("study session: explanation, questions, evaluation, summary, history", async ({ page }) => {
  test.setTimeout(240_000);
  await signUp(page, "Session Tester");
  await uploadCalculus(page);

  // Start from a concept ("Study this concept").
  await page.getByRole("navigation", { name: "Main" }).getByRole("link", { name: "Concepts" }).click();
  await page.getByTestId("concept-row").filter({ hasText: "Limits" }).click();
  await page.getByRole("link", { name: "Study this concept" }).click();
  await expect(page).toHaveURL(/\/session\?concept_id=/);
  await expect(page.getByText("Focused on Limits.")).toBeVisible();
  await expect(page.getByText(/No sessions yet/)).toBeVisible();
  await page.getByRole("button", { name: "Start session" }).click();
  await expect(page).toHaveURL(/\/session\/[0-9a-f-]{36}$/);

  // AC-5.1 / AC-5.6: the explanation streams in over the WebSocket and renders LaTeX.
  await expect(page.getByTestId("connection-status")).toHaveText("Live", { timeout: 15_000 });
  const explanation = page.getByTestId("explanation").first();
  await expect(explanation).toContainText("power rule", { timeout: 30_000 });
  await expect(explanation).toHaveAttribute("data-streaming", "false");
  await expect(explanation.locator(".katex-display")).toHaveCount(1);
  await expect(explanation.locator(".katex").first()).toBeVisible();
  await expect(page.getByRole("progressbar", { name: "Mastery of Limits" })).toBeVisible();

  // Follow-up question while the explanation awaits acknowledgement.
  await page.getByLabel("Ask the tutor a question").fill("Why does the exponent come down?");
  await page.getByRole("button", { name: "Ask" }).click();
  await expect(page.getByTestId("explanation").filter({ hasText: "Good question" })).toBeVisible({ timeout: 30_000 });

  // AC-5.2: a wrong answer is evaluated immediately, with the correct answer shown.
  const wrong = await answer(page, 1);
  await expect(wrong).toHaveAttribute("data-correct", "false");
  await expect(wrong).toContainText("Correct answer");
  await expect(wrong.locator(".katex").first()).toBeVisible();

  // AC-5.4: a right answer raises mastery, visibly.
  const right = await answer(page, 0);
  await expect(right).toHaveAttribute("data-correct", "true");
  await expect(right).toContainText("Correct");
  const bar = right.getByRole("progressbar");
  await expect(bar).not.toHaveAttribute("aria-valuenow", "0");
  await expect(right.getByTestId("mastery-delta")).toContainText("+");

  // AC-5.5: end → summary.
  await expect(page.getByTestId("session-room")).toHaveAttribute("data-busy", "false", { timeout: 30_000 });
  await page.getByRole("button", { name: "End session" }).click();
  await page.getByRole("button", { name: "End session" }).click();
  const summary = page.getByTestId("session-summary");
  await expect(summary).toBeVisible({ timeout: 30_000 });
  await expect(summary).toContainText("power rule");
  await expect(summary).toContainText("You ended the session");
  await expect(summary).toContainText("Questions");
  await expect(summary.getByRole("progressbar", { name: "Mastery of Limits" })).toBeVisible();
  await expect(page.getByTestId("connection-status")).toHaveText("Closed");

  // History lists it; reopening shows the stored summary.
  await page.getByRole("navigation", { name: "Main" }).getByRole("link", { name: "Study session" }).click();
  const item = page.getByTestId("session-history-item").first();
  await expect(item).toContainText("Limits");
  await expect(item).toContainText("Completed");
  await expect(item).toContainText("2 questions, 50% correct");
  await item.getByRole("link", { name: "View summary" }).click();
  await expect(page.getByTestId("session-summary")).toContainText("power rule");

  // The dashboard shows it among recent sessions.
  await page.getByRole("navigation", { name: "Main" }).getByRole("link", { name: "Dashboard" }).click();
  await expect(page.getByTestId("recent-sessions")).toContainText("Limits");
});
