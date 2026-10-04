/**
 * AC-2.7 — the frontend shows the login page, authenticates, and renders the
 * dashboard skeleton. Also exercises AC-2.1 end to end: the first authenticated
 * call provisions the User + StudentProfile the dashboard and profile read back.
 */
import { expect, test } from "@playwright/test";

const password = "correct-horse-battery";

function uniqueEmail(): string {
  return `e2e-${Date.now()}-${Math.random().toString(36).slice(2, 8)}@example.com`;
}

test("anonymous visitors are redirected to the login page", async ({ page }) => {
  await page.goto("/dashboard");

  await expect(page).toHaveURL(/\/login\?next=%2Fdashboard$/);
  await expect(page.getByRole("heading", { name: "School in a Box" })).toBeVisible();
  await expect(page.getByLabel("Email")).toBeVisible();
  await expect(page.getByLabel("Password")).toBeVisible();
});

test("auth cookies are httpOnly and SameSite=Strict", async ({ page, context }) => {
  const email = uniqueEmail();
  await page.goto("/login");
  await page.getByRole("tab", { name: "Create account" }).click();
  await page.getByLabel("Email").fill(email);
  await page.getByLabel("Password").fill(password);
  await page.getByRole("button", { name: "Create account" }).click();
  await expect(page).toHaveURL(/\/dashboard$/);

  const authCookies = (await context.cookies()).filter((c) => c.name.startsWith("sb-"));
  expect(authCookies.length).toBeGreaterThan(0);
  for (const cookie of authCookies.filter((c) => !c.name.endsWith("code-verifier"))) {
    expect(cookie.httpOnly).toBe(true);
    expect(cookie.sameSite).toBe("Strict");
  }
  // Page scripts cannot read the session.
  expect(await page.evaluate(() => document.cookie)).not.toContain("sb-");
});

test("sign up → dashboard skeleton → subjects → profile → sign out", async ({ page }) => {
  const email = uniqueEmail();

  // Sign up (mock auth auto-confirms, like Supabase with confirmations off).
  await page.goto("/login");
  await page.getByRole("tab", { name: "Create account" }).click();
  await page.getByLabel("Your name").fill("Ada Learner");
  await page.getByLabel("Email").fill(email);
  await page.getByLabel("Password").fill(password);
  await page.getByRole("button", { name: "Create account" }).click();

  // Dashboard skeleton, populated from the backend (user provisioned in PostgreSQL).
  await expect(page).toHaveURL(/\/dashboard$/);
  await expect(page.getByTestId("dashboard")).toBeVisible();
  await expect(page.getByRole("heading", { name: "Welcome, Ada Learner" })).toBeVisible();
  await expect(page.getByTestId("current-user")).toContainText("Ada Learner");
  await expect(page.getByRole("navigation", { name: "Main" })).toBeVisible();
  for (const panel of ["Mastery overview", "Due for review", "Recent sessions"]) {
    await expect(page.getByRole("heading", { name: panel })).toBeVisible();
  }
  const subjects = page.getByTestId("subjects-card");
  await expect(subjects.getByText(/No subjects yet/)).toBeVisible();

  // Create a subject through the UI → API → DB round trip.
  await subjects.getByRole("button", { name: "Add subject" }).click();
  await subjects.getByLabel("Subject name").fill("Physics");
  await subjects.getByRole("button", { name: "Save" }).click();
  await expect(subjects.getByText("Physics")).toBeVisible();
  await expect(subjects.getByText("0 courses · 0 concepts")).toBeVisible();

  // Duplicate name is rejected with the API's 409 message.
  await subjects.getByRole("button", { name: "Add subject" }).click();
  await subjects.getByLabel("Subject name").fill("Physics");
  await subjects.getByRole("button", { name: "Save" }).click();
  await expect(subjects.getByText("A subject with this name already exists")).toBeVisible();

  // Profile: defaults from StudentProfile, then update and persist.
  await page.getByRole("navigation", { name: "Main" }).getByRole("link", { name: "Profile" }).click();
  await expect(page).toHaveURL(/\/profile$/);
  await expect(page.getByLabel("Email")).toHaveValue(email);
  await expect(page.getByLabel("Difficulty")).toHaveValue("intermediate");
  await page.getByLabel("Grade level").fill("11th grade");
  await page.getByLabel("Timezone").selectOption("Asia/Kolkata");
  await page.getByRole("button", { name: "Save changes" }).click();
  await expect(page.getByText("Profile saved.")).toBeVisible();

  await page.reload();
  await expect(page.getByLabel("Grade level")).toHaveValue("11th grade");
  await expect(page.getByLabel("Timezone")).toHaveValue("Asia/Kolkata");

  // Sign out → protected pages require login again.
  await page.getByRole("button", { name: "Sign out" }).click();
  await expect(page).toHaveURL(/\/login$/);
  await page.goto("/profile");
  await expect(page).toHaveURL(/\/login\?next=%2Fprofile$/);

  // Sign back in with the same credentials, returning to the requested page.
  await page.getByLabel("Email").fill(email);
  await page.getByLabel("Password").fill(password);
  await page.getByRole("button", { name: "Sign in" }).click();
  await expect(page).toHaveURL(/\/profile$/);
  await expect(page.getByLabel("Grade level")).toHaveValue("11th grade");
});

test("wrong password shows an error and stays on login", async ({ page }) => {
  await page.goto("/login");
  await page.getByLabel("Email").fill("nobody@example.com");
  await page.getByLabel("Password").fill("not-the-password");
  await page.getByRole("button", { name: "Sign in" }).click();

  // (Next's route announcer also has role=alert, so match the message itself.)
  await expect(page.getByText("Incorrect email or password.")).toBeVisible();
  await expect(page).toHaveURL(/\/login/);
});

test("the backend proxy refuses unauthenticated API calls", async ({ request }) => {
  const res = await request.get("/api/backend/profile");
  expect(res.status()).toBe(401);
  expect((await res.json()).error.code).toBe("AUTHENTICATION_REQUIRED");
});
