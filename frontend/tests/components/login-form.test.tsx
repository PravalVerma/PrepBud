import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";

import { LoginForm } from "@/components/auth/login-form";
import type { AuthFormState } from "@/lib/auth";

type Action = (prev: AuthFormState, formData: FormData) => Promise<AuthFormState>;

const actions = vi.hoisted(() => ({
  signIn: vi.fn<Action>(async () => ({ error: "Incorrect email or password." })),
  signUp: vi.fn<Action>(async () => ({
    message: "Check your email for a confirmation link to finish signing up.",
  })),
}));
vi.mock("@/lib/auth", () => actions);

describe("LoginForm", () => {
  it("renders the sign-in form by default", () => {
    render(<LoginForm />);
    expect(screen.getByRole("tab", { name: "Sign in" })).toHaveAttribute("aria-selected", "true");
    expect(screen.getByLabelText("Email")).toHaveAttribute("type", "email");
    expect(screen.getByLabelText("Password")).toHaveAttribute("autocomplete", "current-password");
    expect(screen.queryByLabelText("Your name")).toBeNull();
  });

  it("switches to account creation", async () => {
    render(<LoginForm />);
    await userEvent.setup().click(screen.getByRole("tab", { name: "Create account" }));
    expect(screen.getByLabelText("Your name")).toBeInTheDocument();
    expect(screen.getByLabelText("Password")).toHaveAttribute("autocomplete", "new-password");
  });

  it("shows the sign-in error returned by the server action", async () => {
    render(<LoginForm next="/profile" />);
    const user = userEvent.setup();
    await user.type(screen.getByLabelText("Email"), "a@b.co");
    await user.type(screen.getByLabelText("Password"), "wrong-password");
    await user.click(screen.getByRole("button", { name: "Sign in" }));

    expect(await screen.findByRole("alert")).toHaveTextContent("Incorrect email or password.");
    const formData = actions.signIn.mock.calls[0][1];
    expect(formData.get("next")).toBe("/profile");
    expect(formData.get("email")).toBe("a@b.co");
  });

  it("shows the confirmation message after sign-up", async () => {
    render(<LoginForm />);
    const user = userEvent.setup();
    await user.click(screen.getByRole("tab", { name: "Create account" }));
    await user.type(screen.getByLabelText("Email"), "new@b.co");
    await user.type(screen.getByLabelText("Password"), "long-enough-pw");
    await user.click(screen.getByRole("button", { name: "Create account" }));

    expect(await screen.findByRole("status")).toHaveTextContent(/Check your email/);
  });

  it("shows an initial error passed by the page", () => {
    render(<LoginForm initialError="That confirmation link is invalid" />);
    expect(screen.getByRole("alert")).toHaveTextContent("That confirmation link is invalid");
  });
});
