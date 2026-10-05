/** Learning goals and study plans (API_CONTRACT §3.7, §3.10). */

export type GoalType = "mastery" | "deadline" | "exploration";
export type GoalStatus = "draft" | "active" | "completed" | "paused" | "abandoned";

export interface GoalProgress {
  concept_count: number;
  mastered_count: number;
  average_mastery: number;
  /** 0–1: mean of min(mastery / learned threshold, 1). */
  progress: number;
  days_remaining: number | null;
  all_mastered: boolean;
}

export interface Goal {
  id: string;
  title: string;
  description: string | null;
  goal_type: GoalType;
  target_date: string | null;
  status: GoalStatus;
  subject_id: string | null;
  course_id: string | null;
  target_concept_ids: string[];
  progress: GoalProgress;
  created_at: string | null;
  updated_at: string | null;
  concepts?: { id: string; name: string; mastery_level: number }[] | null;
  study_plan?: { id: string; total_items: number; due_today: number; generated_at: string | null } | null;
}

export interface GoalCreate {
  title: string;
  description?: string | null;
  goal_type?: GoalType;
  target_date?: string | null;
  subject_id?: string | null;
  course_id?: string | null;
  target_concept_ids?: string[];
}

export type GoalUpdate = Partial<GoalCreate> & { status?: GoalStatus };

export type ReviewItemStatus = "pending" | "overdue" | "completed" | "skipped";

export interface ReviewItem {
  id: string;
  concept_id: string;
  concept_name: string;
  scheduled_date: string;
  priority: number;
  status: ReviewItemStatus;
  kind: "learn" | "review";
  goal_id: string | null;
  mastery_level: number;
  mastery_label: string;
  next_review_at: string | null;
  completed_at: string | null;
}

export interface StudyPlanStats {
  total_items: number;
  completed: number;
  skipped: number;
  overdue: number;
  upcoming_today: number;
  due_today: number;
  reviews_due: number;
  next_due_date: string | null;
  estimated_minutes: number;
}

export interface StudyPlan {
  id: string;
  learning_goal_id: string | null;
  status: string;
  generated_at: string | null;
  valid_until: string | null;
  items: ReviewItem[];
  stats: StudyPlanStats;
}

export interface ReviewItemUpdate {
  status?: "pending" | "completed" | "skipped";
  scheduled_date?: string;
}
