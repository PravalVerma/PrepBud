/** Shared E2E steps. */
import path from "node:path";

import { expect, type Locator, type Page } from "@playwright/test";

export const CALCULUS_PDF = path.join(__dirname, "..", "fixtures", "calculus.pdf");

export async function signUp(page: Page, name = "Content Tester"): Promise<void> {
  const email = `e2e-${Date.now()}-${Math.random().toString(36).slice(2, 8)}@example.com`;
  await page.goto("/login");
  await page.getByRole("tab", { name: "Create account" }).click();
  await page.getByLabel("Your name").fill(name);
  await page.getByLabel("Email").fill(email);
  await page.getByLabel("Password").fill("correct-horse-battery");
  await page.getByRole("button", { name: "Create account" }).click();
  // Generous: several specs sign up at once against freshly started servers.
  await expect(page).toHaveURL(/\/dashboard$/, { timeout: 30_000 });
}

/** Uploads the calculus fixture and waits until the pipeline has processed it. */
export async function uploadCalculus(page: Page): Promise<Locator> {
  await page.getByRole("navigation", { name: "Main" }).getByRole("link", { name: "Upload material" }).click();
  await expect(page).toHaveURL(/\/upload$/);
  await page.getByLabel("Choose files to upload").setInputFiles(CALCULUS_PDF);
  const row = page.getByTestId("document-row").filter({ hasText: "calculus" });
  await expect(row).toHaveAttribute("data-status", "ready", { timeout: 90_000 });
  return row;
}

/** Waits for the current turn, acknowledging explanations until a question is open. */
export async function openQuestion(page: Page): Promise<Locator> {
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

export async function answer(page: Page, option: number): Promise<Locator> {
  const card = await openQuestion(page);
  await card.getByTestId("answer-option").nth(option).click();
  await card.getByRole("button", { name: "Submit answer" }).click();
  const evaluation = page.getByTestId("evaluation").last();
  await expect(page.getByTestId("session-room")).toHaveAttribute("data-busy", "false", { timeout: 30_000 });
  return evaluation;
}

/** Ends the open session from the session page and waits for the summary. */
export async function endSession(page: Page): Promise<Locator> {
  await expect(page.getByTestId("session-room")).toHaveAttribute("data-busy", "false", { timeout: 30_000 });
  await page.getByRole("button", { name: "End session" }).click();
  await page.getByRole("button", { name: "End session" }).click();
  const summary = page.getByTestId("session-summary");
  await expect(summary).toBeVisible({ timeout: 30_000 });
  return summary;
}
