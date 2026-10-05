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
  await expect(page).toHaveURL(/\/dashboard$/);
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
