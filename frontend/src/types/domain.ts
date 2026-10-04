/** Domain entity types (docs/DOMAIN_MODEL.md, API_CONTRACT.md §3.3–3.4). */

export type DifficultyBand = "novice" | "beginner" | "intermediate" | "advanced";

export type OnboardingState = "new" | "profile_set" | "first_upload" | "first_session" | "complete";

export type MasteryLabel = "novice" | "beginner" | "intermediate" | "proficient" | "mastered";

export interface Profile {
  id: string;
  email: string;
  display_name: string | null;
  avatar_url: string | null;
  grade_level: string | null;
  difficulty_band: DifficultyBand | null;
  language: string | null;
  timezone: string | null;
  preferences: Record<string, unknown>;
  cumulative_stats: {
    total_sessions?: number;
    total_minutes?: number;
    concepts_mastered?: number;
    [key: string]: unknown;
  };
  onboarding_state: OnboardingState | null;
}

export interface ProfileUpdate {
  display_name?: string | null;
  grade_level?: string | null;
  difficulty_band?: DifficultyBand;
  language?: string;
  timezone?: string;
  preferences?: Record<string, unknown>;
  onboarding_state?: OnboardingState;
}

export interface Subject {
  id: string;
  name: string;
  description: string | null;
  icon: string | null;
  course_count: number;
  concept_count: number;
  avg_mastery: number;
  created_at: string | null;
  updated_at: string | null;
}

export interface SubjectCreate {
  name: string;
  description?: string | null;
  icon?: string | null;
}

interface CurriculumNode {
  id: string;
  name: string;
  description: string | null;
  sort_order: number | null;
  created_at: string | null;
  updated_at: string | null;
}

export interface Course extends CurriculumNode {
  subject_id: string;
}

export interface Chapter extends CurriculumNode {
  course_id: string;
}

export interface Section extends CurriculumNode {
  chapter_id: string;
}

export interface CurriculumNodeCreate {
  name: string;
  description?: string | null;
  sort_order?: number;
}

export interface HealthStatus {
  status: "healthy" | "degraded" | "unhealthy";
  version: string;
  services: Record<string, "ok" | "error" | "not_configured">;
}
