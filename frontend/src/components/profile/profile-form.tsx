"use client";

import { type FormEvent, useMemo, useState } from "react";

import { Button } from "@/components/ui/button";
import { SelectField, TextField } from "@/components/ui/field";
import { Alert } from "@/components/ui/feedback";
import { ApiError } from "@/lib/api";
import type { DifficultyBand, Profile, ProfileUpdate } from "@/types/domain";

const DIFFICULTY_OPTIONS: { value: DifficultyBand; label: string }[] = [
  { value: "novice", label: "Novice — brand new to most topics" },
  { value: "beginner", label: "Beginner" },
  { value: "intermediate", label: "Intermediate" },
  { value: "advanced", label: "Advanced" },
];

const LANGUAGE_OPTIONS = [
  { value: "en", label: "English" },
  { value: "hi", label: "Hindi" },
  { value: "es", label: "Spanish" },
  { value: "fr", label: "French" },
  { value: "de", label: "German" },
];

/** Browsers report some zones by legacy ICU names; prefer current IANA names. */
const LEGACY_ZONE_NAMES: Record<string, string> = {
  "Asia/Calcutta": "Asia/Kolkata",
  "Asia/Katmandu": "Asia/Kathmandu",
  "Asia/Rangoon": "Asia/Yangon",
  "Asia/Saigon": "Asia/Ho_Chi_Minh",
  "Atlantic/Faeroe": "Atlantic/Faroe",
  "Europe/Kiev": "Europe/Kyiv",
  "Pacific/Ponape": "Pacific/Pohnpei",
  "Pacific/Truk": "Pacific/Chuuk",
  "America/Godthab": "America/Nuuk",
};

export function timezoneOptions(current: string): { value: string; label: string }[] {
  const reported =
    typeof Intl.supportedValuesOf === "function" ? Intl.supportedValuesOf("timeZone") : [];
  const zones = new Set(["UTC", current, ...reported.map((z) => LEGACY_ZONE_NAMES[z] ?? z)]);
  return [...zones].sort().map((z) => ({ value: z, label: z.replace(/_/g, " ") }));
}

interface FormValues {
  display_name: string;
  grade_level: string;
  difficulty_band: DifficultyBand;
  language: string;
  timezone: string;
}

function toValues(p: Profile): FormValues {
  return {
    display_name: p.display_name ?? "",
    grade_level: p.grade_level ?? "",
    difficulty_band: p.difficulty_band ?? "intermediate",
    language: p.language ?? "en",
    timezone: p.timezone ?? "UTC",
  };
}

/** Only fields the user actually changed are sent (PATCH semantics). */
export function diffProfile(initial: FormValues, current: FormValues): ProfileUpdate {
  const changes: ProfileUpdate = {};
  if (current.display_name.trim() !== initial.display_name) {
    changes.display_name = current.display_name.trim() || null;
  }
  if (current.grade_level.trim() !== initial.grade_level) {
    changes.grade_level = current.grade_level.trim() || null;
  }
  if (current.difficulty_band !== initial.difficulty_band) changes.difficulty_band = current.difficulty_band;
  if (current.language !== initial.language) changes.language = current.language;
  if (current.timezone !== initial.timezone) changes.timezone = current.timezone;
  return changes;
}

export function ProfileForm({
  profile,
  onSubmit,
  saving,
}: {
  profile: Profile;
  onSubmit: (changes: ProfileUpdate) => Promise<unknown>;
  saving: boolean;
}) {
  const initial = useMemo(() => toValues(profile), [profile]);
  const [values, setValues] = useState<FormValues>(initial);
  const [fieldErrors, setFieldErrors] = useState<Record<string, string>>({});
  const [status, setStatus] = useState<{ tone: "success" | "error"; text: string } | null>(null);
  const tzOptions = useMemo(() => timezoneOptions(values.timezone), [values.timezone]);

  const changes = diffProfile(initial, values);
  const dirty = Object.keys(changes).length > 0;

  function set<K extends keyof FormValues>(key: K, value: FormValues[K]) {
    setValues((v) => ({ ...v, [key]: value }));
    setStatus(null);
  }

  async function handleSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setFieldErrors({});
    try {
      const body: ProfileUpdate = { ...changes };
      if (profile.onboarding_state === "new") body.onboarding_state = "profile_set";
      await onSubmit(body);
      setStatus({ tone: "success", text: "Profile saved." });
    } catch (err) {
      if (err instanceof ApiError) {
        setFieldErrors(err.fieldErrors);
        setStatus({ tone: "error", text: err.message });
      } else {
        setStatus({ tone: "error", text: "Something went wrong. Please try again." });
      }
    }
  }

  return (
    <form onSubmit={handleSubmit} className="space-y-5" aria-label="Profile">
      <TextField label="Email" value={profile.email} disabled readOnly />
      <TextField
        label="Display name"
        name="display_name"
        value={values.display_name}
        onChange={(e) => set("display_name", e.target.value)}
        maxLength={100}
        error={fieldErrors.display_name}
      />
      <TextField
        label="Grade level"
        name="grade_level"
        value={values.grade_level}
        onChange={(e) => set("grade_level", e.target.value)}
        placeholder="e.g. 10th grade, undergraduate"
        maxLength={100}
        error={fieldErrors.grade_level}
      />
      <SelectField
        label="Difficulty"
        name="difficulty_band"
        value={values.difficulty_band}
        onChange={(e) => set("difficulty_band", e.target.value as DifficultyBand)}
        options={DIFFICULTY_OPTIONS}
        hint="Sets the starting level for explanations and questions."
        error={fieldErrors.difficulty_band}
      />
      <div className="grid gap-5 sm:grid-cols-2">
        <SelectField
          label="Language"
          name="language"
          value={values.language}
          onChange={(e) => set("language", e.target.value)}
          options={
            LANGUAGE_OPTIONS.some((o) => o.value === values.language)
              ? LANGUAGE_OPTIONS
              : [{ value: values.language, label: values.language }, ...LANGUAGE_OPTIONS]
          }
          error={fieldErrors.language}
        />
        <SelectField
          label="Timezone"
          name="timezone"
          value={values.timezone}
          onChange={(e) => set("timezone", e.target.value)}
          options={tzOptions}
          error={fieldErrors.timezone}
        />
      </div>

      {status && <Alert tone={status.tone}>{status.text}</Alert>}

      <div className="flex justify-end">
        <Button type="submit" loading={saving} disabled={!dirty && profile.onboarding_state !== "new"}>
          Save changes
        </Button>
      </div>
    </form>
  );
}
