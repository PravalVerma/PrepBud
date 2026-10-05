/** Mastery dashboard, misconceptions and AI usage (API_CONTRACT §3.11–3.13). */

export interface SubjectMastery {
  id: string | null; // null = concepts without a subject ("Unsorted")
  name: string;
  avg_mastery: number;
  concept_count: number;
  mastered_count: number;
  struggling_count: number;
}

export interface DayActivity {
  date: string;
  sessions: number;
  minutes: number;
  questions: number;
  concepts_practiced: number;
}

export interface MasteryOverview {
  subjects: SubjectMastery[];
  overall_stats: {
    total_concepts: number;
    total_mastered: number;
    in_progress: number;
    avg_mastery: number;
    streak_days: number;
    total_study_minutes: number;
    total_sessions: number;
    questions_answered: number;
    accuracy: number;
  };
  mastery_distribution: Record<"novice" | "beginner" | "intermediate" | "proficient" | "mastered", number>;
  recent_activity: DayActivity[];
  misconceptions: Partial<Record<"active" | "recurring" | "resolved", number>>;
  timezone: string;
  generated_at: string;
}

export interface HeatmapConcept {
  id: string;
  name: string;
  subject_id: string | null;
  subject_name: string | null;
  mastery_level: number;
  label: string;
  attempt_count: number;
  last_assessed_at: string | null;
  next_review_at: string | null;
  cells: (number | null)[];
}

export interface MasteryHeatmap {
  columns: string[];
  concepts: HeatmapConcept[];
  total_concepts: number;
  truncated: boolean;
}

export type MisconceptionStatus = "active" | "recurring" | "resolved";

export interface StudentMisconception {
  id: string;
  misconception: { id: string; name: string; description: string; concept_id: string; concept_name: string };
  status: MisconceptionStatus;
  occurrence_count: number;
  detected_at: string | null;
  resolved_at: string | null;
  evidence: { attempt_id: string | null; description: string; detected_at: string | null }[];
}

export type UsagePeriod = "today" | "week" | "month";

export interface UsageBucket {
  cost: number;
  count: number;
  tokens: number;
  failed: number;
}

export interface AIUsage {
  period: UsagePeriod;
  since: string;
  total_cost_usd: number;
  today_cost_usd: number;
  daily_budget_usd: number;
  total_tokens: number;
  total_interactions: number;
  failed_interactions: number;
  by_purpose: Record<string, UsageBucket>;
  by_model: Record<string, UsageBucket>;
  daily: { date: string; cost: number; count: number }[];
}
