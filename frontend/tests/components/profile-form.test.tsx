import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";

import { diffProfile, ProfileForm, timezoneOptions } from "@/components/profile/profile-form";
import { ApiError } from "@/lib/api";
import type { Profile } from "@/types/domain";

const profile: Profile = {
  id: "p1",
  email: "jane@example.com",
  display_name: "Jane",
  avatar_url: null,
  grade_level: "10th grade",
  difficulty_band: "intermediate",
  language: "en",
  timezone: "UTC",
  preferences: {},
  cumulative_stats: {},
  onboarding_state: "profile_set",
};

describe("ProfileForm", () => {
  it("submits only the changed fields", async () => {
    const onSubmit = vi.fn().mockResolvedValue(undefined);
    render(<ProfileForm profile={profile} onSubmit={onSubmit} saving={false} />);
    const user = userEvent.setup();

    await user.clear(screen.getByLabelText("Grade level"));
    await user.type(screen.getByLabelText("Grade level"), "11th grade");
    await user.selectOptions(screen.getByLabelText("Difficulty"), "advanced");
    await user.click(screen.getByRole("button", { name: "Save changes" }));

    expect(onSubmit).toHaveBeenCalledWith({ grade_level: "11th grade", difficulty_band: "advanced" });
    expect(await screen.findByText("Profile saved.")).toBeInTheDocument();
  });

  it("disables saving until something changes", () => {
    render(<ProfileForm profile={profile} onSubmit={vi.fn()} saving={false} />);
    expect(screen.getByRole("button", { name: "Save changes" })).toBeDisabled();
  });

  it("advances onboarding on first save", async () => {
    const onSubmit = vi.fn().mockResolvedValue(undefined);
    render(
      <ProfileForm profile={{ ...profile, onboarding_state: "new" }} onSubmit={onSubmit} saving={false} />,
    );

    await userEvent.setup().click(screen.getByRole("button", { name: "Save changes" }));

    expect(onSubmit).toHaveBeenCalledWith({ onboarding_state: "profile_set" });
  });

  it("shows server-side validation errors next to the field", async () => {
    const onSubmit = vi.fn().mockRejectedValue(
      new ApiError(400, "VALIDATION_ERROR", "Invalid request body or parameters", {
        errors: [{ loc: ["body", "grade_level"], message: "String should have at most 100 characters", type: "x" }],
      }),
    );
    render(<ProfileForm profile={profile} onSubmit={onSubmit} saving={false} />);
    const user = userEvent.setup();

    await user.type(screen.getByLabelText("Grade level"), "!");
    await user.click(screen.getByRole("button", { name: "Save changes" }));

    expect(await screen.findByText("String should have at most 100 characters")).toBeInTheDocument();
    expect(screen.getByLabelText("Grade level")).toHaveAttribute("aria-invalid", "true");
  });

  it("shows the email read-only", () => {
    render(<ProfileForm profile={profile} onSubmit={vi.fn()} saving={false} />);
    expect(screen.getByLabelText("Email")).toHaveValue("jane@example.com");
    expect(screen.getByLabelText("Email")).toBeDisabled();
  });
});

describe("timezoneOptions", () => {
  it("uses current IANA names, keeps the saved zone, and includes UTC once", () => {
    vi.spyOn(Intl, "supportedValuesOf").mockReturnValue(["Asia/Calcutta", "Europe/London", "UTC"]);

    const values = timezoneOptions("Antarctica/Troll").map((o) => o.value);

    expect(values).toEqual(["Antarctica/Troll", "Asia/Kolkata", "Europe/London", "UTC"]);
  });
});

describe("diffProfile", () => {
  const base = {
    display_name: "Jane",
    grade_level: "10th",
    difficulty_band: "intermediate" as const,
    language: "en",
    timezone: "UTC",
  };

  it("returns nothing when unchanged", () => {
    expect(diffProfile(base, { ...base })).toEqual({});
  });

  it("trims and clears text fields to null", () => {
    expect(diffProfile(base, { ...base, display_name: "  ", grade_level: " 11th " })).toEqual({
      display_name: null,
      grade_level: "11th",
    });
  });

  it("includes language and timezone changes", () => {
    expect(diffProfile(base, { ...base, language: "hi", timezone: "Asia/Kolkata" })).toEqual({
      language: "hi",
      timezone: "Asia/Kolkata",
    });
  });
});
