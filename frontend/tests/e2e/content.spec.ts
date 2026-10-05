/**
 * Phase 3 journey (TEST_STRATEGY §3.4 "Document upload"): upload a PDF → watch it go
 * pending → processing → ready (AC-3.1) → browse the extracted concepts (AC-3.2) and
 * their prerequisites (AC-3.3) → see matching sections via hybrid search (AC-3.4).
 *
 * The real pipeline runs: browser → presigned PUT to S3 → Celery worker → text
 * extraction + chunking → OpenAI-compatible LLM (local mock) → PostgreSQL + Qdrant.
 */
import { expect, test } from "@playwright/test";

import { CALCULUS_PDF as PDF, signUp } from "./support/journey";

test("upload a PDF, watch it process, explore its concepts", async ({ page }) => {
  test.setTimeout(120_000);
  await signUp(page);

  await page.getByRole("navigation", { name: "Main" }).getByRole("link", { name: "Upload material" }).click();
  await expect(page).toHaveURL(/\/upload$/);
  await expect(page.getByText(/No documents yet/)).toBeVisible();

  // Browser → presigned PUT straight to S3 → confirm.
  await page.getByLabel("Choose files to upload").setInputFiles(PDF);
  const item = page.getByTestId("upload-item");
  await expect(item.getByText("calculus.pdf")).toBeVisible();
  await expect(item.getByText(/Uploaded — processing/)).toBeVisible({ timeout: 30_000 });

  // The list polls until the worker finishes.
  const row = page.getByTestId("document-row").filter({ hasText: "calculus" });
  await expect(row).toBeVisible();
  await expect(row).toHaveAttribute("data-status", "ready", { timeout: 90_000 });
  await expect(row.getByTestId("document-status")).toHaveText("Ready");
  await expect(row.getByTestId("document-stats")).toContainText("3 pages");
  await expect(row.getByTestId("document-stats")).toContainText("3 concepts");

  // Extracted concepts (AC-3.2).
  await row.getByRole("link", { name: /View concepts/ }).click();
  await expect(page).toHaveURL(/\/concepts\?document_id=/);
  const rows = page.getByTestId("concept-row");
  await expect(rows).toHaveCount(3);
  await expect(rows.filter({ hasText: "Derivatives" })).toContainText("The instantaneous rate of change");

  // Search box narrows the list.
  await page.getByLabel("Search concepts").fill("composition");
  await expect(rows).toHaveCount(1);
  await expect(rows.first()).toContainText("Chain Rule");
  await page.getByLabel("Search concepts").fill("");

  // Prerequisite graph (AC-3.3) and matching material (AC-3.4).
  await rows.filter({ hasText: "Derivatives" }).click();
  await expect(page.getByRole("heading", { name: "Derivatives" })).toBeVisible();
  await expect(page.getByTestId("prerequisites").getByRole("link", { name: "Limits" })).toBeVisible();
  await expect(page.getByTestId("dependents").getByRole("link", { name: "Chain Rule" })).toBeVisible();
  await expect(page.getByTestId("concept-documents")).toContainText("calculus");
  await expect(page.getByTestId("search-hit").first()).toContainText("calculus");

  // Navigate the graph.
  await page.getByTestId("prerequisites").getByRole("link", { name: "Limits" }).click();
  await expect(page.getByRole("heading", { name: "Limits" })).toBeVisible();
  await expect(page.getByText("No prerequisites — a good starting point.")).toBeVisible();

  // Deleting the document removes its concepts.
  await page.getByRole("navigation", { name: "Main" }).getByRole("link", { name: "Upload material" }).click();
  await row.getByRole("button", { name: "Delete" }).click();
  await row.getByRole("button", { name: "Delete" }).click();
  await expect(page.getByText(/No documents yet/)).toBeVisible();
  await page.getByRole("navigation", { name: "Main" }).getByRole("link", { name: "Concepts" }).click();
  await expect(page.getByText(/No concepts yet/)).toBeVisible();
});

test("unsupported files are rejected in the browser", async ({ page }) => {
  await signUp(page);
  await page.goto("/upload");
  await page.getByLabel("Choose files to upload").setInputFiles({
    name: "malware.exe",
    mimeType: "application/octet-stream",
    buffer: Buffer.from("MZ"),
  });
  await expect(page.getByTestId("upload-item").getByRole("alert")).toHaveText(/Unsupported file type/);
});
